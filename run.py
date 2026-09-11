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

import uvicorn
from config import CONFIG
from core.domain.models import TradeLifecycle, SessionConfig, PositionState
from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase
from core.use_cases.execution.circuit_breaker import CircuitBreakerUseCase
from core.use_cases.intelligence.pre_session_planner import PreSessionPlannerUseCase
from core.use_cases.intelligence.hindsight_auditor import HindsightAuditorUseCase

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
            self.state["session_config"] = {
                "session_id": new_plan.session_id,
                "symbol": new_plan.symbol,
                "market_regime": new_plan.market_regime.value,
                "setups_enabled": new_plan.setups_enabled,
                "session_tag": new_plan.session_tag
            }
            await self.event_bus.publish("telemetry", {
                "type": "SESSION_PLAN_RELOADED",
                "session_tag": new_plan.session_tag,
                "regime": new_plan.market_regime.value,
                "reason": "AI Re-Plan Completed after Zone Breach"
            })
            print(f"[ZONE_MONITOR] New AI Session Plan active! New Regime: {new_plan.market_regime.value}")
        except Exception as e:
            print(f"[ZONE_MONITOR ERROR] Async AI Re-Plan failed: {e}")
        finally:
            self.session_manager.is_replanning = False

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
                        self.state["session_config"] = {
                            "session_id": new_plan.session_id,
                            "symbol": new_plan.symbol,
                            "market_regime": new_plan.market_regime.value,
                            "setups_enabled": new_plan.setups_enabled,
                            "session_tag": new_plan.session_tag
                        }
                        session_cfg = new_plan
                        self.session_manager.mark_replan_completed(session_status.session_tag)
                        await self.event_bus.publish("telemetry", {
                            "type": "SESSION_PLAN_RELOADED",
                            "session_tag": session_status.session_tag,
                            "session": session_status.current_session.value,
                            "regime": new_plan.market_regime.value
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

                # 1. Fetch latest bars across 3 timeframes
                m30_bars = await self.broker.get_latest_bars(self.symbol, "M30", 50)
                m3_bars = await self.broker.get_latest_bars(self.symbol, "M3", 60)
                m1_bars = await self.broker.get_latest_bars(self.symbol, "M1", 60)

                # Broadcast live market tick to WebSocket clients
                latest_m1 = m1_bars[-1] if m1_bars else None
                latest_m3 = m3_bars[-1] if m3_bars else None
                latest_m30 = m30_bars[-1] if m30_bars else None
                if latest_m1:
                    await self.event_bus.publish("market_tick", {
                        "symbol": self.symbol,
                        "m1": {"time": int(latest_m1.timestamp), "open": latest_m1.open, "high": latest_m1.high, "low": latest_m1.low, "close": latest_m1.close},
                        "m3": {"time": int(latest_m3.timestamp), "open": latest_m3.open, "high": latest_m3.high, "low": latest_m3.low, "close": latest_m3.close} if latest_m3 else None,
                        "m30": {"time": int(latest_m30.timestamp), "open": latest_m30.open, "high": latest_m30.high, "low": latest_m30.low, "close": latest_m30.close} if latest_m30 else None,
                    })

                # 2. Update active trades lifecycle
                active_trades: List[TradeLifecycle] = self.state["active_trades"]

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
                        print(f"[ENGINE] Trade Finalized: {trade.trade_id} [{trade.state.value}]. Post-Trade Cooldown Active.")

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

                # Tier 2: Async AI Re-Plan when M30 confirms session boundary breakout
                if latest_m30 and session_cfg and not getattr(self.session_manager, "is_replanning", False):
                    should_replan, replan_reason = ZoneMonitor.should_trigger_ai_replan(latest_m30, session_cfg, profile)
                    if should_replan:
                        print(f"[ZONE_MONITOR] Session Boundary Breach! Triggering Tier 2 Async AI Re-Plan: {replan_reason}")
                        asyncio.create_task(self._trigger_async_replan(session_cfg, replan_reason))

                # 4. Evaluate new setup entry
                new_trade = await self.evaluate_entry.execute(
                    config=session_cfg,
                    bars_m30=m30_bars,
                    bars_m3=m3_bars,
                    bars_m1=m1_bars,
                    active_trades=active_trades
                )

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
                        session_cfg = await self.pre_planner.execute(self.symbol, [])
                        self.state["session_config_obj"] = session_cfg
                        sups = session_cfg.support_zones
                        reses = session_cfg.resistance_zones
                        nearest_sup = min(sups, key=lambda s: abs(curr_p - s.high)) if sups else None
                        nearest_res = min(reses, key=lambda r: abs(curr_p - r.low)) if reses else None
                        dist_sup = abs(curr_p - nearest_sup.high) if nearest_sup else 9999
                        dist_res = abs(curr_p - nearest_res.low) if nearest_res else 9999

                    from core.domain.models import get_instrument_profile
                    profile = get_instrument_profile(self.symbol)


                    if dist_sup <= dist_res and nearest_sup:
                        cand_side = "BUY"
                        cand_setup = "TST (Test Support)"
                        expected_entry = nearest_sup.high
                        s1 = round(nearest_sup.low - profile.min_buffer_points, profile.digits)
                        t1 = round(nearest_res.low if nearest_res else (expected_entry + profile.default_t1_points), profile.digits)
                        t2 = round((nearest_res.high if nearest_res else t1) + profile.default_t1_points, profile.digits)
                        lrp = round((s1 + t1) / 2.0, profile.digits)
                        dist = round(curr_p - expected_entry, 2)
                    elif nearest_res:
                        cand_side = "SELL"
                        cand_setup = "TST (Test Resistance)"
                        expected_entry = nearest_res.low
                        s1 = round(nearest_res.high + profile.min_buffer_points, profile.digits)
                        t1 = round(nearest_sup.high if nearest_sup else (expected_entry - profile.default_t1_points), profile.digits)
                        t2 = round((nearest_sup.low if nearest_sup else t1) - profile.default_t1_points, profile.digits)
                        lrp = round((s1 + t1) / 2.0, profile.digits)
                        dist = round(expected_entry - curr_p, 2)
                    else:
                        cand_side, cand_setup, expected_entry, s1, t1, t2, lrp, dist = "NONE", "SCANNING", curr_p, curr_p, curr_p, curr_p, curr_p, 0

                    session_status = self.session_manager.get_session_status(session_cfg)
                    radar_status = "SCANNING_APPROACH"
                    if session_status.in_transition:
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

        # Generate initial pre-session plan if auto_plan enabled
        if auto_plan:
            print("[PLANNER] Generating initial pre-session plan...")
            status = self.session_manager.get_session_status()
            plan = await self.pre_planner.execute(self.symbol, [], session_tag=status.session_tag)
            self.session_manager.mark_replan_completed(status.session_tag)
            self.state["session_config_obj"] = plan
            self.state["session_config"] = {
                "session_id": plan.session_id,
                "symbol": plan.symbol,
                "market_regime": plan.market_regime.value,
                "setups_enabled": plan.setups_enabled,
                "session_tag": plan.session_tag
            }
            print(f"[PLANNER] Initial Plan active: Session {status.current_session.value} ({status.session_tag}), Regime = {plan.market_regime.value}")

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
