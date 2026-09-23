import unittest
import os
import json
import asyncio
from core.domain.models import Bar, SessionConfig, MarketRegime, HTFZone, Significance, OrderSide
from core.domain.rules.zone_monitor import ZoneMonitor
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.storage.json_store import LocalJsonStore
from infrastructure.storage.memory_vector_store import MemoryVectorStore
from infrastructure.storage.json_lesson_rules import JsonLessonRulesStore
from presentation.api.server_a_api import create_server_a_app
from presentation.api.server_b_api import create_server_b_app
from core.use_cases.execution.circuit_breaker import CircuitBreakerUseCase
from infrastructure.bus.async_event_bus import AsyncEventBus
from unittest.mock import AsyncMock, MagicMock

class TestM15AndClearFeatures(unittest.TestCase):
    def setUp(self):
        self.tmp_data_dir = os.path.join(os.path.dirname(__file__), "tmp_test_data")
        os.makedirs(self.tmp_data_dir, exist_ok=True)
        self.json_store = LocalJsonStore(data_dir=self.tmp_data_dir)

    def tearDown(self):
        import shutil
        if os.path.exists(self.tmp_data_dir):
            shutil.rmtree(self.tmp_data_dir, ignore_errors=True)

    def test_paper_broker_generates_m15_bars(self):
        broker = PaperBroker(symbol="XAUUSD")
        bars_m15 = broker._bars_cache.get("M15", [])
        self.assertGreaterEqual(len(bars_m15), 50)
        self.assertEqual(bars_m15[0].timeframe, "M15")

        # Advance tick updates M15
        broker.advance_tick()
        bars_after = broker._bars_cache.get("M15", [])
        self.assertGreaterEqual(len(bars_after), 50)

    def test_zone_monitor_m15_replan_trigger(self):
        cfg = SessionConfig(
            session_id="test_sess",
            symbol="XAUUSD",
            generated_at="2026-09-21T08:00:00Z",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[HTFZone(id="r1", high=2650.0, low=2648.0, significance=Significance.MAJOR)],
            support_zones=[HTFZone(id="s1", high=2630.0, low=2628.0, significance=Significance.MAJOR)],
            setups_enabled={},
            execution_rules={},
            risk_management={},
            news_filter={}
        )

        m15_break_bar = Bar(timestamp=1789000000, open=2649.0, high=2653.0, low=2648.0, close=2652.5, volume=100, timeframe="M15")
        should_replan, reason = ZoneMonitor.should_trigger_ai_replan(curr_bar_htf=m15_break_bar, config=cfg)
        self.assertTrue(should_replan)
        self.assertIn("M15 close 2652.5 broke above max session resistance", reason)

    def test_zone_monitor_m15_find_next_htf_target(self):
        m15_bars = [
            Bar(100, 2600.0, 2610.0, 2595.0, 2605.0, 100, "M15"),
            Bar(200, 2605.0, 2620.0, 2602.0, 2615.0, 100, "M15"),
            Bar(300, 2615.0, 2640.0, 2610.0, 2635.0, 100, "M15"),  # Swing High
            Bar(400, 2635.0, 2630.0, 2615.0, 2620.0, 100, "M15"),
            Bar(500, 2620.0, 2625.0, 2610.0, 2615.0, 100, "M15"),
        ]
        target = ZoneMonitor.find_next_htf_target(bars_htf=m15_bars, side=OrderSide.BUY, current_price=2620.0)
        self.assertGreater(target, 2620.0)

    def test_server_a_clear_trade_history(self):
        broker = AsyncMock()
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()
        cb = CircuitBreakerUseCase(broker, event_bus)
        state_ref = {
            "symbol": "XAUUSD",
            "active_trades": [],
            "closed_trades": [
                {"trade_id": "trade_1", "state": "FULLY_CLOSED"},
                {"trade_id": "trade_2", "state": "STOPPED_OUT"}
            ],
            "session_config": {}
        }
        # Save dummy initial trades
        self.json_store.save_session_trades(state_ref["closed_trades"])

        app = create_server_a_app(broker, event_bus, cb, state_ref, json_store=self.json_store)
        
        # Check initial history
        history_route = [r for r in app.routes if getattr(r, "path", None) == "/api/trades/history" and "GET" in r.methods][0]
        res_initial = asyncio.run(history_route.endpoint())
        self.assertEqual(res_initial["total_closed"], 2)

        # Find and call clear route
        clear_route = [r for r in app.routes if getattr(r, "path", None) == "/api/trades/history/clear" and "POST" in r.methods][0]
        res_clear = asyncio.run(clear_route.endpoint())
        self.assertEqual(res_clear["status"], "SUCCESS")

        # Check history after clearing
        res_after = asyncio.run(history_route.endpoint())
        self.assertEqual(res_after["total_closed"], 0)
        self.assertEqual(state_ref["closed_trades"], [])
        self.assertEqual(self.json_store.load_session_trades(), [])

    def test_server_b_clear_lessons(self):
        planner = MagicMock()
        auditor = MagicMock()
        vstore = MemoryVectorStore()
        # Add sample document to vector store
        asyncio.run(vstore.add_lesson("Test lesson rule for pullback", {"regime": "GENERAL"}))

        # Create dummy session_lessons and last_audit_report
        with open(os.path.join(self.tmp_data_dir, "session_lessons.json"), "w") as f:
            f.write(json.dumps(["Lesson 1"]))
        with open(os.path.join(self.tmp_data_dir, "last_audit_report.json"), "w") as f:
            f.write(json.dumps({"session_id": "s1"}))

        eval_mock = MagicMock()
        eval_mock.compiled_lesson_rules = ["rule1", "rule2"]

        app = create_server_b_app(planner, auditor, vstore, self.json_store, evaluate_entry=eval_mock)
        
        # Find and call clear lessons route
        clear_route = [r for r in app.routes if getattr(r, "path", None) == "/api/lessons/clear" and "POST" in r.methods][0]
        res_clear = asyncio.run(clear_route.endpoint())
        self.assertEqual(res_clear["status"], "SUCCESS")

        # Verify vstore is cleared
        self.assertEqual(vstore._documents, [])
        self.assertEqual(eval_mock.compiled_lesson_rules, [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp_data_dir, "session_lessons.json")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp_data_dir, "last_audit_report.json")))

    def test_wholesale_tp90_sl120_scaling(self):
        from core.domain.rules.wholesale_engine import WholesaleEngine
        from core.domain.models import SetupType

        # BUY Setup test
        ws_buy = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.TST,
            side=OrderSide.BUY,
            pullback_swing_price=2645.0,
            t1_price=2660.0,
            t2_price=2670.0,
            micro_stall_high=2651.0,
            micro_stall_low=2650.0,
            buffer_pts=0.0,
            min_rr_ratio=0.75,
            sl_multiplier=1.20,
            tp_multiplier=0.90
        )
        # Entry = 2650.0
        self.assertEqual(ws_buy.recommended_entry, 2650.0)
        # Raw SL dist = 2650 - 2645 = 5.0 -> Scaled SL dist = 5.0 * 1.20 = 6.0 -> S1 = 2644.0
        self.assertEqual(ws_buy.S1, 2644.0)
        # Raw T1 dist = 2660 - 2650 = 10.0 -> Scaled T1 dist = 10.0 * 0.90 = 9.0 -> T1 = 2659.0
        self.assertEqual(ws_buy.T1, 2659.0)
        # Raw T2 dist = 2670 - 2650 = 20.0 -> Scaled T2 dist = 20.0 * 0.90 = 18.0 -> T2 = 2668.0
        self.assertEqual(ws_buy.T2, 2668.0)
        # R:R = 9.0 / 6.0 = 1.50 >= 0.75
        self.assertEqual(ws_buy.rr_ratio_part1, 1.50)
        self.assertTrue(ws_buy.is_valid_entry)
        self.assertEqual(ws_buy.sl_multiplier, 1.20)
        self.assertEqual(ws_buy.tp_multiplier, 0.90)
        self.assertEqual(ws_buy.min_rr_ratio, 0.75)

        # SELL Setup test
        ws_sell = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.TST,
            side=OrderSide.SELL,
            pullback_swing_price=2655.0,
            t1_price=2640.0,
            t2_price=2630.0,
            micro_stall_high=2650.0,
            micro_stall_low=2649.0,
            buffer_pts=0.0,
            min_rr_ratio=0.75,
            sl_multiplier=1.20,
            tp_multiplier=0.90
        )
        # Entry = 2650.0
        self.assertEqual(ws_sell.recommended_entry, 2650.0)
        # Raw SL dist = 2655 - 2650 = 5.0 -> Scaled SL dist = 5.0 * 1.20 = 6.0 -> S1 = 2656.0
        self.assertEqual(ws_sell.S1, 2656.0)
        # Raw T1 dist = 2650 - 2640 = 10.0 -> Scaled T1 dist = 10.0 * 0.90 = 9.0 -> T1 = 2641.0
        self.assertEqual(ws_sell.T1, 2641.0)
        # Raw T2 dist = 2650 - 2630 = 20.0 -> Scaled T2 dist = 20.0 * 0.90 = 18.0 -> T2 = 2632.0
        self.assertEqual(ws_sell.T2, 2632.0)
        self.assertEqual(ws_sell.rr_ratio_part1, 1.50)
        self.assertTrue(ws_sell.is_valid_entry)

    def test_pre_entry_scorer_with_075_min_rr(self):
        from core.domain.rules.pre_entry_scorer import PreEntryScorer
        from core.domain.models import get_instrument_profile

        profile = get_instrument_profile("XAUUSD")

        # Case 1: R:R = 0.70 (< min_rr 0.75) -> Must HARD FAIL
        res_fail = PreEntryScorer.evaluate(
            setup="TST",
            side="BUY",
            regime="SIDEWAYS_RANGE",
            wholesale={
                "is_valid_entry": True,
                "recommended_entry": 2650.0,
                "S1": 2644.0,
                "T1": 2654.2,
                "LWP": 2651.0,
                "LRP": 2650.0,
                "rr_ratio_part1": 0.70,
                "min_rr_ratio": 0.75
            },
            profile=profile
        )
        self.assertFalse(res_fail.approved)
        self.assertIn("thấp hơn mức tối thiểu 0.75", res_fail.rejection_reason)

        # Case 2: R:R = 0.80 (>= min_rr 0.75) -> Approved
        res_pass = PreEntryScorer.evaluate(
            setup="TST",
            side="BUY",
            regime="SIDEWAYS_RANGE",
            wholesale={
                "is_valid_entry": True,
                "recommended_entry": 2650.0,
                "S1": 2644.0,
                "T1": 2655.0,
                "LWP": 2651.0,
                "LRP": 2650.0,
                "rr_ratio_part1": 0.80,
                "min_rr_ratio": 0.75
            },
            profile=profile
        )
        self.assertTrue(res_pass.approved)

    def test_risk_manager_lot_sizing_preserves_dollar_risk(self):
        from core.domain.rules.risk_manager import RiskManager
        from core.domain.models import get_instrument_profile

        profile = get_instrument_profile("XAUUSD")
        balance = 10000.0
        risk_pct = 1.0  # $100 risk

        # Base SL dist = 5.0 (SL = 2645.0)
        lot_base, _, _ = RiskManager.calculate_lot_size(
            balance=balance,
            risk_percent=risk_pct,
            entry_price=2650.0,
            sl_price=2645.0,
            point_size=profile.point,
            tick_value=profile.tick_value
        )

        # 120% Widened SL dist = 6.0 (SL = 2644.0)
        lot_widened, _, _ = RiskManager.calculate_lot_size(
            balance=balance,
            risk_percent=risk_pct,
            entry_price=2650.0,
            sl_price=2644.0,
            point_size=profile.point,
            tick_value=profile.tick_value
        )

        # With wider SL, lot size must decrease proportionally
        self.assertLess(lot_widened, lot_base)
        # Risk amount for widened: 6.0 * lot_widened * 100 <= $100
        dollar_risk = abs(2650.0 - 2644.0) * lot_widened * (profile.tick_value / profile.point)
        self.assertLessEqual(dollar_risk, balance * (risk_pct / 100.0) + 1e-2)

if __name__ == "__main__":
    unittest.main()

