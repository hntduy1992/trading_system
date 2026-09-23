"""
=============================================================================
YTC PRICE ACTION TRADER (Lance Beggs) - SINGLE ENTRYPOINT ORCHESTRATOR
Version: 2.1.0-STRICT
Ports: Server A (29120), Server B (29121) [No conflict with common dev ports]
=============================================================================
"""
import sys
import os
import asyncio
import argparse
import signal
import time
from typing import Dict, Any, List, Optional

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Silence harmless Windows asyncio WinError 10054 connection reset bug & force UTF-8 stdout
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        from asyncio.proactor_events import _ProactorBasePipeTransport
        _orig_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost

        def _silenced_call_connection_lost(self, *args, **kwargs):
            try:
                return _orig_call_connection_lost(self, *args, **kwargs)
            except (ConnectionResetError, OSError):
                pass

        _ProactorBasePipeTransport._call_connection_lost = _silenced_call_connection_lost
    except Exception:
        pass

import uvicorn
from config import CONFIG
from core.domain.models import TradeLifecycle, SessionConfig, PositionState, OrderSide, SetupType, PositionPart, session_config_to_dict
from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase
from core.use_cases.execution.circuit_breaker import CircuitBreakerUseCase
from core.use_cases.intelligence.pre_session_planner import PreSessionPlannerUseCase
from core.use_cases.intelligence.hindsight_auditor import HindsightAuditorUseCase
from core.domain.rules.candlestick_engine import CandlestickEngine

from infrastructure.bus.async_event_bus import AsyncEventBus
from infrastructure.storage.memory_vector_store import MemoryVectorStore
from infrastructure.storage.json_store import LocalJsonStore
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.brokers.mt5_broker import MT5Broker
from infrastructure.ai.mock_ai_adapter import MockAIEngine
from infrastructure.ai.gemini_adapter import GeminiAIAdapter

from presentation.api.server_a_api import create_server_a_app
from presentation.api.server_b_api import create_server_b_app

class SystemOrchestrator:
    def __init__(self, mode: str = "paper", symbol: str = "XAUUSD", active_env_file: Optional[str] = None):
        self.mode = mode
        self.symbol = symbol
        self.active_env_file = active_env_file or os.path.join(BASE_DIR, ".env")

        self.running = True

        # Shared System State
        self.state: Dict[str, Any] = {
            "symbol": symbol,
            "mode": mode,
            "session_config": None,
            "active_trades": [],
            "closed_trades": []
        }

        # 1. Dependency Injection: Core Infrastructure
        self.event_bus = AsyncEventBus()
        self.vector_store = MemoryVectorStore()
        self.json_store = LocalJsonStore(os.path.join(BASE_DIR, "data"))

        # Broker selection (Port & Adapter)
        if mode == "live":
            print("[INIT] Initializing Live MetaTrader 5 Gateway...")
            self.broker = MT5Broker(
                login=CONFIG.broker.MT5_LOGIN,
                password=CONFIG.broker.MT5_PASSWORD,
                server=CONFIG.broker.MT5_SERVER,
                path=CONFIG.broker.MT5_PATH
            )
        else:
            print("[INIT] Initializing In-Memory Paper Broker Simulation...")
            self.broker = PaperBroker(initial_balance=10000.0, symbol=symbol)

        # AI Engine selection
        ai_provider = os.getenv("AI_PROVIDER", CONFIG.ai.PROVIDER).lower()
        gemini_key = os.getenv("GEMINI_API_KEY", CONFIG.ai.GEMINI_API_KEY)
        openai_key = os.getenv("OPENAI_API_KEY", CONFIG.ai.OPENAI_API_KEY)
        model_name = os.getenv("AI_MODEL_NAME", CONFIG.ai.MODEL_NAME)

        if ai_provider == "openai" and openai_key:
            from infrastructure.ai.openai_adapter import OpenAIAdapter
            print(f"[INIT] Initializing OpenAI Strategy Adapter ({model_name})...")
            self.ai_engine = OpenAIAdapter(api_key=openai_key, model_name=model_name)
        elif ai_provider == "gemini" and gemini_key:
            print(f"[INIT] Initializing Gemini AI Strategy Adapter ({model_name})...")
            self.ai_engine = GeminiAIAdapter(api_key=gemini_key, model_name=model_name)
        else:
            print("[INIT] No valid AI API key detected or provider='mock'. Initializing Mock AI Strategy Adapter (Offline Deterministic)...")
            self.ai_engine = MockAIEngine()

        # 2. Dependency Injection: Use Cases
        from core.domain.rules.session_manager import SessionManager
        self.session_manager = SessionManager(enabled=True)
        self.evaluate_entry = EvaluateEntryUseCase(self.broker, self.event_bus, ai_engine=self.ai_engine)
        self.evaluate_entry.session_manager = self.session_manager
        self.manage_lifecycle = ManageLifecycleUseCase(self.broker, self.event_bus)
        self.circuit_breaker = CircuitBreakerUseCase(self.broker, self.event_bus)
        self.pre_planner = PreSessionPlannerUseCase(self.ai_engine, self.vector_store, self.broker)
        self.auditor = HindsightAuditorUseCase(self.ai_engine, self.vector_store)
        from core.use_cases.intelligence.news_sentiment_analyzer import NewsSentimentAnalyzerUseCase
        self.news_analyzer = NewsSentimentAnalyzerUseCase(self.ai_engine)
        self.last_news_refresh_time = 0.0

        # 3. Presentation FastAPI Apps
        self.server_a_app = create_server_a_app(
            broker=self.broker,
            event_bus=self.event_bus,
            circuit_breaker=self.circuit_breaker,
            state_ref=self.state,
            json_store=self.json_store
        )

        self.server_b_app = create_server_b_app(
            planner=self.pre_planner,
            auditor=self.auditor,
            vector_store=self.vector_store,
            json_store=self.json_store,
            active_env_file=self.active_env_file,
            evaluate_entry=self.evaluate_entry
        )

    async def _trigger_async_replan(self, session_cfg: SessionConfig, reason: str):
        """Tier 2 Async AI Re-Plan: Runs in background without blocking Server A execution."""
        if getattr(self.session_manager, "is_replanning", False):
            return
        self.session_manager.is_replanning = True
        try:
            await self.event_bus.publish("telemetry", {
                "type": "ASYNC_REPLAN_STARTED",
                "reason": reason
            })
            new_plan = await self.pre_planner.execute(self.symbol, [], session_tag=session_cfg.session_tag)
            self.state["session_config_obj"] = new_plan
            self.state["session_config"] = session_config_to_dict(new_plan)
            await self.event_bus.publish("telemetry", {
                "type": "SESSION_PLAN_RELOADED",
                "session_tag": new_plan.session_tag,
                "regime": new_plan.market_regime.value,
                "config": self.state["session_config"],
                "reason": "AI Re-Plan Completed after Zone Breach"
            })
            print(f"[ZONE_MONITOR] New AI Session Plan active! New Regime: {new_plan.market_regime.value}")
        except Exception as e:
            print(f"[ZONE_MONITOR ERROR] Async AI Re-Plan failed: {e}")
        finally:
            self.session_manager.is_replanning = False

    async def refresh_news_calendar(self, force: bool = False):
        """Refreshes economic calendar and news blackout windows periodically (every 15 min)."""
        now = time.time()
        if force or (now - self.last_news_refresh_time > 900):
            try:
                analysis = await self.news_analyzer.execute(
                    symbol=self.symbol,
                    include_medium_impact=True,
                    force_refresh=force
                )
                self.last_news_refresh_time = now
                session_cfg: SessionConfig = self.state.get("session_config_obj")
                if session_cfg:
                    if not getattr(session_cfg, "news_filter", None):
                        session_cfg.news_filter = {}
                    session_cfg.news_filter["blackout_windows"] = analysis.get("blackout_windows", [])
                    session_cfg.news_filter["macro_bias"] = analysis.get("macro_bias", "NEUTRAL")
                    session_cfg.news_filter["lot_multiplier"] = analysis.get("lot_multiplier", 1.0)
                    session_cfg.news_filter["recommendation"] = analysis.get("recommendation_summary", "")
                bw_count = len(analysis.get("blackout_windows", []))
                print(f"[NEWS_ENGINE] Refreshed economic calendar. Active blackout windows: {bw_count}")
            except Exception as e:
                print(f"[NEWS_ENGINE ERROR] Could not refresh calendar: {e}")

    async def reconcile_and_scan_broker_positions(self, latest_m1_close: float):
        """
        Reverse Scanning & Reconciliation Mechanism:
        1. Scans MT5 broker positions & orders directly.
        2. Reconciles with active_trades: detects broker-side closes (SL/TP/Manual).
        3. Scans MT5 positions: detects orphaned positions (closed on Server but NOT closed on MT5)
           and executes immediate FORCE-CLOSE retry on MT5!
        4. Recovers/adopts legitimately untracked MT5 positions into active_trades to protect capital.
        """
        if not hasattr(self.broker, "get_open_positions"):
            return

        try:
            broker_positions = await self.broker.get_open_positions(self.symbol)
            active_trades: List[TradeLifecycle] = self.state["active_trades"]
            closed_trades: List[TradeLifecycle] = self.state["closed_trades"]

            # Map of open positions by ticket and identifier
            broker_ticket_set = set()
            for bp in broker_positions:
                broker_ticket_set.add(bp["ticket"])
                if bp.get("identifier"):
                    broker_ticket_set.add(bp["identifier"])

            # --- SUB-PASS 1: Check active_trades on Server against MT5 ---
            for trade in list(active_trades):
                if trade.state in [PositionState.IN_POSITION, PositionState.TRAILING_STOP]:
                    trade_tickets = {t for t in [trade.part1.ticket, trade.part2.ticket, trade.limit_order_ticket] if t}
                    is_open_on_broker = any(t in broker_ticket_set for t in trade_tickets)
                    if not is_open_on_broker and trade.trade_id:
                        is_open_on_broker = any(
                            trade.trade_id in (bp.get("comment") or "") for bp in broker_positions
                        )

                    # If not open on broker and not yet marked closed locally -> Broker closed it!
                    if not is_open_on_broker and not trade.part1.is_closed:
                        close_price = latest_m1_close if latest_m1_close > 0 else trade.part1.entry_price
                        close_reason = "BROKER_EXECUTED_CLOSE"
                        final_state = PositionState.STOPPED_OUT
                        try:
                            import MetaTrader5 as mt5
                            import datetime
                            now_dt = datetime.datetime.now()
                            deals = mt5.history_deals_get(now_dt - datetime.timedelta(minutes=30), now_dt)
                            if deals:
                                for d in reversed(deals):
                                    if d.position_id in trade_tickets or (d.comment and trade.trade_id and trade.trade_id in d.comment):
                                        close_price = d.price
                                        c_str = (d.comment or "").lower()
                                        if "[sl" in c_str:
                                            close_reason = "BROKER_SL_HIT"
                                            final_state = PositionState.STOPPED_OUT
                                        elif "[tp" in c_str:
                                            close_reason = "BROKER_TP_HIT"
                                            final_state = PositionState.FULLY_CLOSED
                                        else:
                                            close_reason = f"BROKER_CLOSE_{d.comment}" if d.comment else "BROKER_MARKET_CLOSE"
                                            final_state = PositionState.SCRATCHED if d.profit >= 0 else PositionState.STOPPED_OUT
                                        break
                        except Exception:
                            pass

                        trade.state = final_state
                        trade.close_time = time.time()
                        trade.part1.is_closed = True
                        trade.part1.close_price = close_price
                        trade.part2.is_closed = True
                        trade.part2.close_price = close_price
                        trade.close_context = {
                            "close_state": final_state.value,
                            "close_reason": close_reason,
                            "exit_price_part1": close_price,
                            "exit_price_part2": close_price,
                            "bars_in_trade": trade.m1_bars_in_trade,
                            "close_time": trade.close_time,
                            "close_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade.close_time))
                        }
                        print(f"[RECONCILIATION] MT5 confirmed closed position: {trade.trade_id} ({close_reason} at {close_price})")

            # --- SUB-PASS 2: REVERSE SCAN: Check MT5 positions against Server ---
            # Detect orphaned positions: open on MT5 but marked closed on Server!
            for bp in broker_positions:
                bp_ticket = bp["ticket"]
                bp_ident = bp.get("identifier", bp_ticket)
                bp_comment = bp.get("comment") or ""

                # Is this MT5 position in active_trades?
                in_active = any(
                    bp_ticket in [t.part1.ticket, t.part2.ticket, t.limit_order_ticket] or
                    bp_ident in [t.part1.ticket, t.part2.ticket, t.limit_order_ticket] or
                    (t.trade_id and t.trade_id in bp_comment)
                    for t in active_trades
                )

                if in_active:
                    continue  # Healthy active trade

                # If NOT in active_trades, check if it belongs to a CLOSED trade
                matched_closed = None
                for ct in closed_trades:
                    if isinstance(ct, dict):
                        ct_l = ct.get("limit_order_ticket")
                        ct_p1 = ct.get("part1", {}).get("ticket", 0)
                        ct_p2 = ct.get("part2", {}).get("ticket", 0)
                        ct_tid = ct.get("trade_id")
                    else:
                        ct_l = getattr(ct, "limit_order_ticket", None)
                        ct_p1 = ct.part1.ticket if hasattr(ct, "part1") and ct.part1 else 0
                        ct_p2 = ct.part2.ticket if hasattr(ct, "part2") and ct.part2 else 0
                        ct_tid = getattr(ct, "trade_id", None)
                        
                    if (
                        bp_ticket in [ct_p1, ct_p2, ct_l] or
                        bp_ident in [ct_p1, ct_p2, ct_l] or
                        (ct_tid and ct_tid in bp_comment)
                    ):
                        matched_closed = ct
                        break

                if matched_closed is not None:
                    # CRITICAL DISCREPANCY: LỆNH ĐÃ ĐÓNG Ở HỆ THỐNG NHƯNG MT5 KHÔNG ĐÓNG!
                    ct_tid = matched_closed.get("trade_id") if isinstance(matched_closed, dict) else getattr(matched_closed, "trade_id", "UNKNOWN")
                    print(f"[REVERSE_SCANNER DISCREPANCY] MT5 Position {bp_ticket} is OPEN on broker but trade {ct_tid} is already CLOSED on Server! Executing FORCE-CLOSE retry...")
                    closed_ok = await self.broker.close_position(bp_ticket, bp.get("volume"))
                    if closed_ok:
                        print(f"[REVERSE_SCANNER SUCCESS] Force-closed orphaned MT5 position {bp_ticket} successfully.")
                        await self.event_bus.publish("telemetry", {
                            "type": "ORPHAN_POSITION_FORCE_CLOSED",
                            "ticket": bp_ticket,
                            "trade_id": ct_tid,
                            "message": f"Đã quét ngược và đóng cưỡng bức thành công lệnh mồ côi MT5 ticket {bp_ticket}."
                        })
                    else:
                        print(f"[REVERSE_SCANNER CRITICAL] Failed to close orphaned MT5 position {bp_ticket}! Will retry next tick.")
                        await self.event_bus.publish("telemetry", {
                            "type": "ORPHAN_POSITION_CLOSE_FAILED",
                            "ticket": bp_ticket,
                            "trade_id": ct_tid,
                            "message": f"Lỗi: Máy chủ không thể đóng lệnh mồ côi MT5 ticket {bp_ticket}! Hãy kiểm tra MT5."
                        })
                    # Adopt all unmanaged positions for this symbol so it does not float unmanaged!
                    print(f"[REVERSE_SCANNER RECOVERY] Found unmanaged MT5 position {bp_ticket} ({bp_comment}). Adopting into active_trades!")
                    side = OrderSide.BUY if bp["type"] == "BUY" else OrderSide.SELL
                    vol = bp["volume"]
                    lot_p1 = round(vol * 0.5, 2)
                    lot_p2 = round(vol - lot_p1, 2)
                    trade_id = bp_comment if bp_comment.startswith("ytc_") else f"recovered_{bp_ticket}"
                    recovered_trade = TradeLifecycle(
                        trade_id=trade_id,
                        symbol=self.symbol,
                        setup_type=SetupType.TST if "TST" in trade_id else SetupType.PB,
                        side=side,
                        state=PositionState.IN_POSITION,
                        part1=PositionPart(1, lot_p1, bp["price_open"], bp["sl"], bp["tp"], ticket=bp_ticket),
                        part2=PositionPart(2, lot_p2, bp["price_open"], bp["sl"], bp["tp"], ticket=bp_ticket),
                        open_time=bp.get("time", time.time()),
                        entry_context={"recovered": True, "ticket": bp_ticket, "volume": vol}
                    )
                    active_trades.append(recovered_trade)
                    await self.event_bus.publish("telemetry", {
                        "type": "POSITION_RECOVERED",
                        "ticket": bp_ticket,
                        "trade_id": trade_id,
                        "message": f"🔄 Đã khôi phục và tiếp quản lệnh MT5 ticket {bp_ticket} vào hệ thống quản trị."
                    })

            # --- SUB-PASS 3: REVERSE SCAN: Check MT5 open orders (Pending Orders) ---
            if hasattr(self.broker, "get_open_orders"):
                broker_orders = await self.broker.get_open_orders(self.symbol)
                for bo in broker_orders:
                    bo_ticket = bo["ticket"]
                    bo_comment = bo.get("comment") or ""
                    
                    # Is this MT5 order in active_trades?
                    in_active = any(
                        bo_ticket in [t.limit_order_ticket, t.stop_order_ticket, t.part1.ticket, t.part2.ticket] or
                        (t.trade_id and t.trade_id in bo_comment)
                        for t in active_trades
                    )
                    
                    if in_active:
                        continue
                    
                    # Not in active_trades, maybe in closed?
                    matched_closed = None
                    for ct in closed_trades:
                        if isinstance(ct, dict):
                            ct_l_ticket = ct.get("limit_order_ticket")
                            ct_s_ticket = ct.get("stop_order_ticket")
                            ct_p1 = ct.get("part1", {}).get("ticket", 0)
                            ct_p2 = ct.get("part2", {}).get("ticket", 0)
                            ct_tid = ct.get("trade_id")
                        else:
                            ct_l_ticket = getattr(ct, "limit_order_ticket", None)
                            ct_s_ticket = getattr(ct, "stop_order_ticket", None)
                            ct_p1 = ct.part1.ticket if hasattr(ct, "part1") and ct.part1 else 0
                            ct_p2 = ct.part2.ticket if hasattr(ct, "part2") and ct.part2 else 0
                            ct_tid = getattr(ct, "trade_id", None)
                            
                        if (bo_ticket in [ct_l_ticket, ct_s_ticket, ct_p1, ct_p2] or
                            (ct_tid and ct_tid in bo_comment)):
                            matched_closed = ct
                            break
                    
                    if matched_closed is not None:
                        print(f"[REVERSE_SCANNER DISCREPANCY] MT5 Order {bo_ticket} is PENDING but trade {ct_tid} is CLOSED on Server! Cancelling...")
                        cancelled_ok = await self.broker.cancel_order(bo_ticket)
                        if cancelled_ok:
                            print(f"[REVERSE_SCANNER SUCCESS] Cancelled orphaned MT5 order {bo_ticket} successfully.")
                    else:
                        print(f"[REVERSE_SCANNER RECOVERY] Found unmanaged MT5 pending order {bo_ticket} ({bo_comment}). Adopting into active_trades!")
                        # ORDER_TYPE: 2=BUY_LIMIT, 3=SELL_LIMIT, 4=BUY_STOP, 5=SELL_STOP, 6=BUY_STOP_LIMIT, 7=SELL_STOP_LIMIT
                        side = OrderSide.BUY if bo["type"] in [2, 4, 6] else OrderSide.SELL
                        vol = bo["volume_current"]
                        lot_p1 = round(vol * 0.5, 2)
                        lot_p2 = round(vol - lot_p1, 2)
                        trade_id = bo_comment if bo_comment.startswith("ytc_") else f"recovered_order_{bo_ticket}"
                        recovered_trade = TradeLifecycle(
                            trade_id=trade_id,
                            symbol=self.symbol,
                            setup_type=SetupType.TST if "TST" in trade_id else SetupType.PB,
                            side=side,
                            state=PositionState.PENDING_ENTRY,
                            part1=PositionPart(1, lot_p1, bo["price_open"], bo["sl"], bo["tp"], ticket=bo_ticket),
                            part2=PositionPart(2, lot_p2, bo["price_open"], bo["sl"], bo["tp"], ticket=bo_ticket),
                            open_time=bo.get("time_setup", time.time()),
                            entry_context={"recovered": True, "ticket": bo_ticket, "volume": vol},
                            max_bars_pending=8,
                            limit_order_ticket=bo_ticket if bo["type"] in [2, 3] else None,
                            stop_order_ticket=bo_ticket if bo["type"] in [4, 5] else None
                        )
                        active_trades.append(recovered_trade)
                        await self.event_bus.publish("telemetry", {
                            "type": "ORDER_RECOVERED",
                            "ticket": bo_ticket,
                            "trade_id": trade_id,
                            "message": f"🔄 Đã tiếp quản lệnh chờ (Pending) MT5 ticket {bo_ticket} vào hệ thống."
                        })

            # Broadcast MT5 sync state
            await self.event_bus.publish("mt5_sync_status", {
                "open_positions_count": len(broker_positions),
                "active_trades_count": len(active_trades),
                "closed_trades_count": len(closed_trades),
                "timestamp": time.time()
            })

        except Exception as e:
            print(f"[REVERSE_SCANNER ERROR] Error during broker reconciliation: {e}")

    async def execution_engine_loop(self):
        """
        Server A Real-time Deterministic Execution Loop:
        Polls bars, updates trade lifecycle, searches for wholesale setups, triggers OCO.
        """
        print(f"[ENGINE] Server A Execution Loop running for {self.symbol}...")
        while self.running:
            try:
                if self.circuit_breaker.is_paused:
                    await asyncio.sleep(1.0)
                    continue

                session_cfg: SessionConfig = self.state.get("session_config_obj")

                # Check Session Transition & Auto Re-Plan on Session Stabilization
                session_status = self.session_manager.get_session_status(session_cfg)
                if self.session_manager.should_trigger_auto_replan(session_status, session_cfg):
                    print(f"[SESSION_MANAGER] New session {session_status.current_session.value} stabilized! Auto-generating fresh AI Session Plan ({session_status.session_tag})...")
                    self.session_manager.mark_replan_started()
                    try:
                        new_plan = await self.pre_planner.execute(self.symbol, [], session_tag=session_status.session_tag)
                        self.state["session_config_obj"] = new_plan
                        self.state["session_config"] = session_config_to_dict(new_plan)
                        session_cfg = new_plan
                        self.session_manager.mark_replan_completed(session_status.session_tag)
                        await self.event_bus.publish("telemetry", {
                            "type": "SESSION_PLAN_RELOADED",
                            "session_tag": session_status.session_tag,
                            "session": session_status.current_session.value,
                            "regime": new_plan.market_regime.value,
                            "config": self.state["session_config"]
                        })
                        print(f"[SESSION_MANAGER] New plan {session_status.session_tag} active! Trading resumed for {session_status.current_session.value}.")
                    except Exception as replan_err:
                        print(f"[SESSION_MANAGER ERROR] Auto Re-Plan failed: {replan_err}")
                        self.session_manager.is_replanning = False

                if not session_cfg:
                    # Waiting for pre-session plan deployment
                    await asyncio.sleep(1.0)
                    continue

                # If in paper mode, advance tick simulation periodically
                if self.mode == "paper" and isinstance(self.broker, PaperBroker):
                    self.broker.advance_tick()

                # Periodic news calendar refresh (every 15 min)
                await self.refresh_news_calendar()
                blackout_st = self.news_analyzer.check_blackout_status()
                is_in_blackout = blackout_st.get("is_in_blackout", False)

                # 1. Fetch latest bars across 3 timeframes (HTF M15, TTF M3, LTF M1)
                m15_bars = await self.broker.get_latest_bars(self.symbol, "M15", 60)
                m3_bars = await self.broker.get_latest_bars(self.symbol, "M3", 60)
                m1_bars = await self.broker.get_latest_bars(self.symbol, "M1", 60)

                # Broadcast live market tick to WebSocket clients
                latest_m1 = m1_bars[-1] if m1_bars else None
                latest_m3 = m3_bars[-1] if m3_bars else None
                latest_m15 = m15_bars[-1] if m15_bars else None
                if latest_m1:
                    await self.event_bus.publish("market_tick", {
                        "symbol": self.symbol,
                        "m1": {"time": int(latest_m1.timestamp), "open": latest_m1.open, "high": latest_m1.high, "low": latest_m1.low, "close": latest_m1.close},
                        "m3": {"time": int(latest_m3.timestamp), "open": latest_m3.open, "high": latest_m3.high, "low": latest_m3.low, "close": latest_m3.close} if latest_m3 else None,
                        "m15": {"time": int(latest_m15.timestamp), "open": latest_m15.open, "high": latest_m15.high, "low": latest_m15.low, "close": latest_m15.close} if latest_m15 else None,
                        "m30": {"time": int(latest_m15.timestamp), "open": latest_m15.open, "high": latest_m15.high, "low": latest_m15.low, "close": latest_m15.close} if latest_m15 else None,
                    })

                # 2. Update active trades lifecycle & MT5 Reverse Reconciliation
                active_trades: List[TradeLifecycle] = self.state["active_trades"]

                # 2a. Broker Reverse Reconciliation: scan MT5 positions, handle orphans, sync state
                await self.reconcile_and_scan_broker_positions(latest_m1.close if latest_m1 else 0.0)

                for trade in list(active_trades):
                    await self.manage_lifecycle.update(trade, m1_bars, m3_bars)
                    if trade.state in [PositionState.FULLY_CLOSED, PositionState.SCRATCHED, PositionState.STOPPED_OUT]:
                        self.evaluate_entry.record_trade_closed(trade)
                        if trade in active_trades:
                            active_trades.remove(trade)
                        self.state["closed_trades"].append(trade)
                        try:
                            from presentation.api.server_a_api import serialize_trade
                            serialized_closed = [serialize_trade(t) for t in self.state["closed_trades"]]
                            self.json_store.save_session_trades(serialized_closed)
                        except Exception as ex:
                            print(f"[ENGINE] Failed to persist closed trades: {ex}")
                        await self.event_bus.publish("trade_closed", {
                            "trade_id": trade.trade_id,
                            "state": trade.state.value,
                            "close_context": getattr(trade, "close_context", {})
                        })
                        is_prof = (trade.total_pnl > 0.0)
                        status_msg = "Profitable Exit - Ready for Next Trade" if is_prof else "Post-Trade Cooldown Active"
                        print(f"[ENGINE] Trade Finalized: {trade.trade_id} [{trade.state.value}] (PnL: {trade.total_pnl:+.2f}$). {status_msg}.")

                # 3. Zone Monitor & S/R Role Reversal (Tier 1 Local Reflex & Tier 2 AI Trigger)
                from core.domain.rules.zone_monitor import ZoneMonitor
                from core.domain.models import get_instrument_profile
                profile = get_instrument_profile(self.symbol)

                # Tier 1: Local S/R Role Reversal when M3 candle confirms breach
                if latest_m3 and session_cfg:
                    breach_res = ZoneMonitor.evaluate_zone_breaches(latest_m3, session_cfg, profile)
                    if breach_res["has_flipped"]:
                        for ev in breach_res["events"]:
                            await self.event_bus.publish("telemetry", ev)
                            print(f"[ZONE_MONITOR] {ev['action']} at {ev['price_level']:.2f}! Market Regime: {session_cfg.market_regime.value}")

                # Tier 2: Async AI Re-Plan when M15 confirms session boundary breakout
                if latest_m15 and session_cfg and not getattr(self.session_manager, "is_replanning", False):
                    should_replan, replan_reason = ZoneMonitor.should_trigger_ai_replan(latest_m15, session_cfg, profile)
                    if should_replan:
                        print(f"[ZONE_MONITOR] Session Boundary Breach! Triggering Tier 2 Async AI Re-Plan: {replan_reason}")
                        asyncio.create_task(self._trigger_async_replan(session_cfg, replan_reason))

                # 4. Evaluate new setup entry (Strictly paused if in News Blackout Window)
                new_trade = None
                if not is_in_blackout:
                    new_trade = await self.evaluate_entry.execute(
                        config=session_cfg,
                        bars_m15=m15_bars,
                        bars_m30=m15_bars,
                        bars_m3=m3_bars,
                        bars_m1=m1_bars,
                        active_trades=active_trades
                    )
                else:
                    # In news blackout: Do not enter new trades
                    pass

                if new_trade:
                    active_trades.append(new_trade)
                    print(f"[ENGINE] New Trade Created: {new_trade.trade_id} [{new_trade.setup_type.value} {new_trade.side.value}]")

                # 4. Compute Setup Radar (Expected entry, SL, TP1, TP2, LRP for visual chart overlays)
                radar = None
                if active_trades and any(t.state in [PositionState.IN_POSITION, PositionState.PENDING_ENTRY, PositionState.TRAILING_STOP] for t in active_trades):
                    active = next(t for t in active_trades if t.state in [PositionState.IN_POSITION, PositionState.PENDING_ENTRY, PositionState.TRAILING_STOP])
                    radar = {
                        "status": "ACTIVE_TRADE" if active.state == PositionState.IN_POSITION else ("TRAILING" if active.state == PositionState.TRAILING_STOP else "ORDER_PENDING"),
                        "setup": f"{active.setup_type.value} ({active.side.value})",
                        "side": active.side.value,
                        "entry": active.part1.entry_price,
                        "s1": active.part2.sl_price or active.part1.sl_price,
                        "t1": active.part1.tp_price,
                        "t2": active.part2.tp_price,
                        "lrp": None,
                        "lot_total": round(active.part1.lot_size + active.part2.lot_size, 2),
                        "lot_p1": active.part1.lot_size,
                        "lot_p2": active.part2.lot_size,
                        "current_price": latest_m1.close if latest_m1 else None,
                        "dist_to_entry": round(abs(latest_m1.close - active.part1.entry_price), 2) if latest_m1 else 0
                    }
                elif getattr(self.evaluate_entry, "candidate_setup", None):
                    cand = self.evaluate_entry.candidate_setup
                    curr_p = latest_m1.close if latest_m1 else cand["entry_price"]
                    dist = round(abs(curr_p - cand["entry_price"]), 2)
                    radar = {
                        "status": f"RÌNH_VÀO_LỆNH (Chờ chạm {cand['entry_price']:.2f})",
                        "setup": f"{cand['setup_name']} ({cand['side'].value})",
                        "side": cand["side"].value,
                        "entry": cand["entry_price"],
                        "s1": cand["sl"],
                        "t1": cand["tp1"],
                        "t2": cand["tp2"],
                        "lrp": None,
                        "lot_total": cand["lot_total"],
                        "lot_p1": cand["lot_p1"],
                        "lot_p2": cand["lot_p2"],
                        "current_price": curr_p,
                        "dist_to_entry": dist,
                        "micro_candle": CandlestickEngine.extract_micro_candle_context(m1_bars) if m1_bars else {}
                    }
                elif session_cfg and latest_m1:
                    curr_p = latest_m1.close
                    sups = session_cfg.support_zones
                    reses = session_cfg.resistance_zones

                    nearest_sup = min(sups, key=lambda s: abs(curr_p - s.high)) if sups else None
                    nearest_res = min(reses, key=lambda r: abs(curr_p - r.low)) if reses else None

                    dist_sup = abs(curr_p - nearest_sup.high) if nearest_sup else 9999
                    dist_res = abs(curr_p - nearest_res.low) if nearest_res else 9999

                    # Auto-heal: If plan zones are >10% away from live market, re-generate plan from live rates
                    if (dist_sup > curr_p * 0.10) and (dist_res > curr_p * 0.10):
                        _heal_status = self.session_manager.get_session_status()
                        session_cfg = await self.pre_planner.execute(self.symbol, [], session_tag=_heal_status.session_tag)
                        self.state["session_config_obj"] = session_cfg
                        self.state["session_config"] = session_config_to_dict(session_cfg)
                        await self.event_bus.publish("telemetry", {
                            "type": "SESSION_PLAN_RELOADED",
                            "session_tag": _heal_status.session_tag,
                            "regime": session_cfg.market_regime.value,
                            "config": self.state["session_config"],
                            "reason": "Auto-heal plan zones to live rates"
                        })
                        sups = session_cfg.support_zones
                        reses = session_cfg.resistance_zones
                        nearest_sup = min(sups, key=lambda s: abs(curr_p - s.high)) if sups else None
                        nearest_res = min(reses, key=lambda r: abs(curr_p - r.low)) if reses else None
                        dist_sup = abs(curr_p - nearest_sup.high) if nearest_sup else 9999
                        dist_res = abs(curr_p - nearest_res.low) if nearest_res else 9999

                    from core.domain.models import get_instrument_profile
                    profile = get_instrument_profile(self.symbol)


                    sl_mult = getattr(CONFIG.risk, "SL_MULTIPLIER", 1.20)
                    tp_mult = getattr(CONFIG.risk, "TP_MULTIPLIER", 0.90)
                    min_rr = getattr(CONFIG.risk, "MIN_RR_RATIO_PART1", 0.75)

                    if dist_sup <= dist_res and nearest_sup:
                        cand_side = "BUY"
                        cand_setup = "TST (Test Support)"
                        expected_entry = nearest_sup.high
                        s1_raw = nearest_sup.low - profile.min_buffer_points
                        scaled_risk = max(expected_entry - s1_raw, 0.00001) * sl_mult
                        s1 = round(expected_entry - scaled_risk, profile.digits)

                        raw_t1 = nearest_res.low if nearest_res else (expected_entry + profile.default_t1_points)
                        scaled_t1_reward = max(raw_t1 - expected_entry, 0.0) * tp_mult
                        t1 = round(expected_entry + scaled_t1_reward, profile.digits)

                        raw_t2 = (nearest_res.high if nearest_res else raw_t1) + profile.default_t1_points
                        scaled_t2_reward = max(raw_t2 - expected_entry, 0.0) * tp_mult
                        t2 = round(expected_entry + scaled_t2_reward, profile.digits)

                        lrp = round((t1 + min_rr * s1) / (1.0 + min_rr), profile.digits)
                        dist = round(curr_p - expected_entry, 2)
                    elif nearest_res:
                        cand_side = "SELL"
                        cand_setup = "TST (Test Resistance)"
                        expected_entry = nearest_res.low
                        s1_raw = nearest_res.high + profile.min_buffer_points
                        scaled_risk = max(s1_raw - expected_entry, 0.00001) * sl_mult
                        s1 = round(expected_entry + scaled_risk, profile.digits)

                        raw_t1 = nearest_sup.high if nearest_sup else (expected_entry - profile.default_t1_points)
                        scaled_t1_reward = max(expected_entry - raw_t1, 0.0) * tp_mult
                        t1 = round(expected_entry - scaled_t1_reward, profile.digits)

                        raw_t2 = (nearest_sup.low if nearest_sup else raw_t1) - profile.default_t1_points
                        scaled_t2_reward = max(expected_entry - raw_t2, 0.0) * tp_mult
                        t2 = round(expected_entry - scaled_t2_reward, profile.digits)

                        lrp = round((t1 + min_rr * s1) / (1.0 + min_rr), profile.digits)
                        dist = round(expected_entry - curr_p, 2)
                    else:
                        cand_side, cand_setup, expected_entry, s1, t1, t2, lrp, dist = "NONE", "SCANNING", curr_p, curr_p, curr_p, curr_p, curr_p, 0

                    session_status = self.session_manager.get_session_status(session_cfg)
                    radar_status = "SCANNING_APPROACH"
                    if is_in_blackout:
                        b_title = blackout_st.get("title") or "Tin tức lớn"
                        radar_status = f"⛔ NÉ TIN: {b_title}"
                    elif session_status.in_transition:
                        rem_m = int(session_status.seconds_until_stabilized // 60)
                        rem_s = int(session_status.seconds_until_stabilized % 60)
                        radar_status = f"GIAO_PHIÊN ({session_status.transition_name} - {rem_m:02d}m{rem_s:02d}s)"
                    elif not session_status.is_plan_loaded:
                        radar_status = f"CHỜ_PLAN ({session_status.current_session.value})"
                    else:
                        max_losses = session_cfg.risk_management.get("max_consecutive_losses", 2) if session_cfg and session_cfg.risk_management else 2
                        if self.evaluate_entry.consecutive_losses >= max_losses:
                            radar_status = "CIRCUIT_BREAKER_PAUSED"
                        else:
                            if getattr(self.evaluate_entry, "last_trade_was_profitable", False):
                                cooldown_secs = session_cfg.execution_rules.get("profitable_close_cooldown_seconds", 0) if session_cfg and session_cfg.execution_rules else 0
                            elif self.evaluate_entry.last_closed_state == PositionState.SCRATCHED:
                                cooldown_secs = session_cfg.execution_rules.get("scratch_cooldown_seconds", 300) if session_cfg and session_cfg.execution_rules else 300
                            elif self.evaluate_entry.last_closed_state == PositionState.STOPPED_OUT:
                                cooldown_secs = session_cfg.execution_rules.get("stopout_cooldown_seconds", 420) if session_cfg and session_cfg.execution_rules else 420
                            else:
                                cooldown_secs = session_cfg.execution_rules.get("post_trade_cooldown_seconds", 180) if session_cfg and session_cfg.execution_rules else 180

                            now = time.time()
                            time_since_close = now - self.evaluate_entry.last_trade_closed_time
                            if self.evaluate_entry.last_trade_closed_time > 0 and time_since_close < cooldown_secs:
                                rem = int(cooldown_secs - time_since_close)
                                radar_status = f"COOLDOWN ({rem}s)"
                            elif abs(dist) <= profile.sr_proximity_points:
                                radar_status = "READY_TO_FIRE"

                    radar = {
                        "status": radar_status,
                        "setup": cand_setup,
                        "side": cand_side,
                        "entry": expected_entry,
                        "s1": s1,
                        "t1": t1,
                        "t2": t2,
                        "lrp": lrp,
                        "current_price": curr_p,
                        "dist_to_entry": dist,
                        "lot_total": 0.17 if "XAU" in self.symbol else 0.50,
                        "lot_p1": 0.08 if "XAU" in self.symbol else 0.25,
                        "lot_p2": 0.09 if "XAU" in self.symbol else 0.25,
                        "micro_candle": CandlestickEngine.extract_micro_candle_context(m1_bars) if m1_bars else {}
                    }

                self.state["setup_radar"] = radar
                if radar:
                    await self.event_bus.publish("setup_radar", radar)


            except Exception as e:
                print(f"[ENGINE ERROR] {e}")

            await asyncio.sleep(1.0)

    async def run_server_a(self, port: int):
        config = uvicorn.Config(
            app=self.server_a_app,
            host="0.0.0.0",
            port=port,
            log_level="warning",
            access_log=False
        )
        server = uvicorn.Server(config)
        await server.serve()

    async def run_server_b(self, port: int):
        config = uvicorn.Config(
            app=self.server_b_app,
            host="0.0.0.0",
            port=port,
            log_level="warning",
            access_log=False
        )
        server = uvicorn.Server(config)
        await server.serve()

    async def start(self, server_a_port: int, server_b_port: int, auto_plan: bool = True):
        # Connect broker
        await self.broker.connect()

        # Startup Reconciliation: Recover any existing open positions from broker
        if hasattr(self.broker, "get_open_positions"):
            try:
                open_pos = await self.broker.get_open_positions(self.symbol)
                for p in open_pos:
                    ticket = p["ticket"]
                    side = OrderSide.BUY if p["type"] == "BUY" else OrderSide.SELL
                    vol = p["volume"]
                    lot_p1 = round(vol * 0.5, 2)
                    lot_p2 = round(vol - lot_p1, 2)
                    trade_id = p.get("comment", "")
                    if not trade_id or not trade_id.startswith("ytc_"):
                        trade_id = f"recovered_{ticket}"

                    if not any(t.part1.ticket == ticket or t.part2.ticket == ticket for t in self.state["active_trades"]):
                        from core.domain.models import SetupType, PositionPart, PositionState, TradeLifecycle
                        recovered = TradeLifecycle(
                            trade_id=trade_id,
                            symbol=self.symbol,
                            setup_type=SetupType.TST if "TST" in trade_id else SetupType.PB,
                            side=side,
                            state=PositionState.IN_POSITION,
                            part1=PositionPart(1, lot_p1, p["price_open"], p["sl"], p["tp"], ticket=ticket),
                            part2=PositionPart(2, lot_p2, p["price_open"], p["sl"], p["tp"], ticket=ticket),
                            open_time=p.get("time", time.time()),
                            entry_context={"recovered": True, "ticket": ticket, "volume": vol}
                        )
                        self.state["active_trades"].append(recovered)
                        print(f"[RECONCILIATION] Restored active position from broker: Ticket {ticket} ({side.value} {vol} lot, SL: {p['sl']}, TP: {p['tp']})")
            except Exception as e:
                print(f"[RECONCILIATION ERROR] Could not restore open positions on startup: {e}")

        # Refresh news calendar and blackout windows on startup
        await self.refresh_news_calendar(force=True)

        # Generate initial pre-session plan if auto_plan enabled
        if auto_plan:
            print("[PLANNER] Generating initial pre-session plan...")
            status = self.session_manager.get_session_status()
            plan = await self.pre_planner.execute(self.symbol, [], session_tag=status.session_tag)
            self.session_manager.mark_replan_completed(status.session_tag)
            self.state["session_config_obj"] = plan
            self.state["session_config"] = session_config_to_dict(plan)
            if hasattr(self, "json_store") and self.json_store:
                try:
                    self.json_store.save_session_config(self.state["session_config"])
                except Exception:
                    pass
            print(f"[PLANNER] Initial Plan active: Session {status.current_session.value} ({status.session_tag}), Regime = {plan.market_regime.value} | S/R Zones: {len(plan.support_zones)} SUP, {len(plan.resistance_zones)} RES")

        print("\n" + "=" * 70)
        print("  YTC PRICE ACTION TRADING SYSTEM ACTIVATED")
        print(f"  - Mode: {self.mode.upper()}")
        print(f"  - Symbol: {self.symbol}")
        print(f"  - Server A (Execution & Dashboard): http://127.0.0.1:{server_a_port}")
        print(f"  - Server B (AI Brain & Audit):      http://127.0.0.1:{server_b_port}")
        print(f"  - WebSocket Telemetry:             ws://127.0.0.1:{server_a_port}/ws/telemetry")
        print("=" * 70 + "\n")

        # Automatically open browser dashboard
        async def _open_browser_when_ready():
            await asyncio.sleep(1.2)
            try:
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{server_a_port}")
            except Exception:
                pass
        asyncio.create_task(_open_browser_when_ready())

        # Run all components concurrently
        await asyncio.gather(
            self.run_server_a(server_a_port),
            self.run_server_b(server_b_port),
            self.execution_engine_loop()
        )

    async def stop(self):
        self.running = False
        print("\n[SHUTDOWN] Stopping all services safely...")
        await self.broker.disconnect()
        print("[SHUTDOWN] Complete.")

def main():
    parser = argparse.ArgumentParser(description="YTC Price Action Trader (v2.1.0-STRICT)")
    parser.add_argument("--env", choices=["paper", "live"], default=None, help="Deployment environment to load (.env.paper or .env.live)")
    parser.add_argument("--env-file", default=None, help="Explicit path to custom environment file")
    parser.add_argument("--mode", choices=["paper", "live"], default=None, help="Trading mode: 'paper' (Simulation) or 'live' (MT5)")
    parser.add_argument("--symbol", default=None, help="Symbol to trade (default from env or XAUUSD)")
    parser.add_argument("--server-a-port", type=int, default=None, help="Port for Server A & Dashboard")
    parser.add_argument("--server-b-port", type=int, default=None, help="Port for Server B")
    parser.add_argument("--no-auto-plan", action="store_true", help="Do not auto-generate plan on startup")
    
    # CLI API Key and AI options
    parser.add_argument("--gemini-api-key", default=None, help="Google Gemini API Key override")
    parser.add_argument("--openai-api-key", default=None, help="OpenAI API Key override")
    parser.add_argument("--ai-provider", choices=["gemini", "openai", "mock"], default=None, help="AI Provider override")
    parser.add_argument("--ai-model", default=None, help="AI Model name override")
    parser.add_argument("--setup", action="store_true", help="Run interactive environment setup wizard before starting")

    args = parser.parse_args()

    # If --setup requested, run the wizard
    if args.setup:
        import setup_env
        setup_env.run_wizard()

    # Determine environment file to load
    from config import reload_config, ACTIVE_ENV_FILE
    chosen_env = args.env_file or args.env or args.mode or "paper"
    cfg = reload_config(env_name_or_path=chosen_env, mode=args.mode or args.env)

    # CLI overrides for API keys and provider
    if args.gemini_api_key:
        os.environ["GEMINI_API_KEY"] = args.gemini_api_key
    if args.openai_api_key:
        os.environ["OPENAI_API_KEY"] = args.openai_api_key
    if args.ai_provider:
        os.environ["AI_PROVIDER"] = args.ai_provider
    if args.ai_model:
        os.environ["AI_MODEL_NAME"] = args.ai_model

    # Resolve final mode, symbol, and ports from args or active config
    from config import ACTIVE_ENV_FILE as resolved_env_file
    final_mode = args.mode or args.env or cfg.broker.MODE or "paper"
    final_symbol = args.symbol or cfg.broker.SYMBOL or "XAUUSD"
    server_a_port = args.server_a_port or cfg.network.SERVER_A_PORT or 29120
    server_b_port = args.server_b_port or cfg.network.SERVER_B_PORT or 29121

    print(f"[CONFIG] Active Environment File: {resolved_env_file}")
    print(f"[CONFIG] Mode: {final_mode.upper()} | Symbol: {final_symbol}")

    orchestrator = SystemOrchestrator(mode=final_mode, symbol=final_symbol, active_env_file=resolved_env_file)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    if sys.platform == "win32":
        def _loop_exception_handler(loop_instance, context):
            exc = context.get("exception")
            if isinstance(exc, ConnectionResetError) or (
                isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10054
            ):
                return
            loop_instance.default_exception_handler(context)

        loop.set_exception_handler(_loop_exception_handler)

    try:
        loop.run_until_complete(orchestrator.start(
            server_a_port=server_a_port,
            server_b_port=server_b_port,
            auto_plan=not args.no_auto_plan
        ))
    except (KeyboardInterrupt, SystemExit):
        loop.run_until_complete(orchestrator.stop())
    finally:
        loop.close()

if __name__ == "__main__":
    main()
