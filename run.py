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
from typing import Dict, Any, List

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
    def __init__(self, mode: str = "paper", symbol: str = "XAUUSD"):
        self.mode = mode
        self.symbol = symbol

        self.running = True

        # Shared System State
        self.state: Dict[str, Any] = {
            "symbol": symbol,
            "mode": mode,
            "session_config": None,
            "active_trades": []
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
        if CONFIG.ai.GEMINI_API_KEY:
            print("[INIT] Initializing Gemini AI Strategy Adapter...")
            self.ai_engine = GeminiAIAdapter(api_key=CONFIG.ai.GEMINI_API_KEY, model_name=CONFIG.ai.MODEL_NAME)
        else:
            print("[INIT] Initializing Mock AI Strategy Adapter (Offline Deterministic)...")
            self.ai_engine = MockAIEngine()

        # 2. Dependency Injection: Use Cases
        self.evaluate_entry = EvaluateEntryUseCase(self.broker, self.event_bus)
        self.manage_lifecycle = ManageLifecycleUseCase(self.broker, self.event_bus)
        self.circuit_breaker = CircuitBreakerUseCase(self.broker, self.event_bus)
        self.pre_planner = PreSessionPlannerUseCase(self.ai_engine, self.vector_store, self.broker)
        self.auditor = HindsightAuditorUseCase(self.ai_engine, self.vector_store)

        # 3. Presentation FastAPI Apps
        self.server_a_app = create_server_a_app(
            broker=self.broker,
            event_bus=self.event_bus,
            circuit_breaker=self.circuit_breaker,
            state_ref=self.state
        )

        self.server_b_app = create_server_b_app(
            planner=self.pre_planner,
            auditor=self.auditor,
            vector_store=self.vector_store,
            json_store=self.json_store
        )

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
                        # Save to session trade history
                        pass

                # 3. Evaluate new setup entry
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

                    radar = {
                        "status": "READY_TO_FIRE" if abs(dist) <= profile.sr_proximity_points else "SCANNING_APPROACH",
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
            plan = await self.pre_planner.execute(self.symbol, [])
            self.state["session_config_obj"] = plan
            self.state["session_config"] = {
                "session_id": plan.session_id,
                "symbol": plan.symbol,
                "market_regime": plan.market_regime.value,
                "setups_enabled": plan.setups_enabled
            }
            print(f"[PLANNER] Plan active: Regime = {plan.market_regime.value}")

        print("\n" + "=" * 70)
        print("  YTC PRICE ACTION TRADING SYSTEM ACTIVATED")
        print(f"  - Mode: {self.mode.upper()}")
        print(f"  - Symbol: {self.symbol}")
        print(f"  - Server A (Execution & Dashboard): http://127.0.0.1:{server_a_port}")
        print(f"  - Server B (AI Brain & Audit):      http://127.0.0.1:{server_b_port}")
        print(f"  - WebSocket Telemetry:             ws://127.0.0.1:{server_a_port}/ws/telemetry")
        print("=" * 70 + "\n")

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
    parser.add_argument("--mode", choices=["paper", "live"], default="paper", help="Trading mode: 'paper' or 'live' (MT5)")
    parser.add_argument("--symbol", default="XAUUSD", help="Symbol to trade (default: XAUUSD)")
    parser.add_argument("--server-a-port", type=int, default=29120, help="Port for Server A & Dashboard (default: 29120)")

    parser.add_argument("--server-b-port", type=int, default=29121, help="Port for Server B (default: 29121)")
    parser.add_argument("--no-auto-plan", action="store_true", help="Do not auto-generate plan on startup")

    args = parser.parse_args()

    orchestrator = SystemOrchestrator(mode=args.mode, symbol=args.symbol)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(orchestrator.start(
            server_a_port=args.server_a_port,
            server_b_port=args.server_b_port,
            auto_plan=not args.no_auto_plan
        ))
    except (KeyboardInterrupt, SystemExit):
        loop.run_until_complete(orchestrator.stop())
    finally:
        loop.close()

if __name__ == "__main__":
    main()
