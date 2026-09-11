"""
Unit tests for Pre-Entry AI Gatekeeper (EvaluateEntryUseCase & AI Adapters)
"""
import unittest
import asyncio
import time
from typing import Dict, Any, List, Optional
from core.domain.models import (
    Bar, SwingNode, SwingType, SessionConfig, MarketRegime, HTFZone, Significance,
    OrderSide, SetupType, PositionState, PreEntryEvaluation, TradeLifecycle
)
from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
from infrastructure.ai.mock_ai_adapter import MockAIEngine
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.bus.async_event_bus import AsyncEventBus


class DummyAIEngine:
    """Configurable AI Engine for testing specific responses and delays."""
    def __init__(self, approved: bool = True, confidence: float = 0.85, delay: float = 0.0):
        self.approved = approved
        self.confidence = confidence
        self.delay = delay
        self.call_count = 0
        self.last_candidate_context = None

    async def evaluate_candidate_trade(
        self,
        candidate_context: Dict[str, Any],
        trading_config: Dict[str, Any],
        recent_bars: Dict[str, Any]
    ) -> PreEntryEvaluation:
        self.call_count += 1
        self.last_candidate_context = candidate_context
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        return PreEntryEvaluation(
            approved=self.approved,
            confidence=self.confidence,
            reason="AI Approval Test" if self.approved else "AI Veto Test: Poor wholesale setup",
            concerns=[] if self.approved else ["Target is blocked by resistance zone"],
            model_name="dummy-tester"
        )


class TestPreEntryAIGatekeeper(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.event_bus = AsyncEventBus()
        self.broker = PaperBroker(initial_balance=10000.0, symbol="XAUUSD")
        self.published_events = []

        # Intercept telemetry events
        async def on_telemetry(payload):
            self.published_events.append(payload)

        self.event_bus.subscribe("telemetry", on_telemetry)

        self.session_config = SessionConfig(
            session_id="sess_pre_entry_test",
            symbol="XAUUSD",
            generated_at="2026-09-11",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[HTFZone("R1", 2670.0, 2665.0)],
            support_zones=[HTFZone("S1", 2645.0, 2640.0)],
            setups_enabled={"TST": True, "BOF": True, "PB": True, "CPB": True, "BPB": True},
            execution_rules={
                "min_rr_ratio_part1": 1.0,
                "enable_session_transition_guard": False,
                "post_trade_cooldown_seconds": 0
            },
            risk_management={
                "account_risk_limit_percent": 1.0,
                "enable_ai_pre_entry": True,
                "min_ai_confidence": 0.65,
                "fixed_lot_size": 0.1
            },
            news_filter={},
            session_tag="LDN_TEST"
        )

    def tearDown(self):
        self.loop.close()

    def _build_test_bars(self):
        """Builds M30, M3, M1 bars that form a valid TST Buy setup near support."""
        bars_m3 = [
            Bar(300, 2655.0, 2660.0, 2650.0, 2652.0),
            Bar(600, 2652.0, 2655.0, 2646.0, 2648.0),
            Bar(900, 2648.0, 2650.0, 2640.0, 2645.0), # Swing Low at 2640.0 inside S1 [2640, 2645]
            Bar(1200, 2645.0, 2652.0, 2644.0, 2649.0),
            Bar(1500, 2649.0, 2655.0, 2646.0, 2650.0),
        ]
        bars_m1 = [
            Bar(1440, 2645.5, 2646.0, 2644.5, 2645.0),
            Bar(1500, 2645.0, 2645.5, 2644.0, 2644.8)
        ]
        return [], bars_m3, bars_m1

    def test_mock_ai_engine_evaluate_candidate_trade(self):
        """Tests MockAIEngine evaluate_candidate_trade logic directly."""
        mock_ai = MockAIEngine()
        candidate = {
            "symbol": "XAUUSD",
            "setup": "TST",
            "side": "BUY",
            "order_price": 2641.0,
            "sl": 2638.0,
            "tp1": 2645.0,
            "wholesale": {"is_valid_entry": True, "rr_ratio_part1": 1.33}
        }
        cfg = {"symbol": "XAUUSD", "setups_enabled": {"TST": True}}
        recent = {}

        res = self.loop.run_until_complete(mock_ai.evaluate_candidate_trade(candidate, cfg, recent))
        self.assertIsInstance(res, PreEntryEvaluation)
        self.assertTrue(res.approved)
        self.assertGreaterEqual(res.confidence, 0.65)
        self.assertIn("Wholesale", res.reason)

        # Test setup disabled
        cfg_disabled = {"symbol": "XAUUSD", "setups_enabled": {"TST": False}}
        res_disabled = self.loop.run_until_complete(mock_ai.evaluate_candidate_trade(candidate, cfg_disabled, recent))
        self.assertFalse(res_disabled.approved)
        self.assertIn("vô hiệu hóa", res_disabled.reason)

    def test_pre_entry_ai_approved_flow(self):
        """When AI approves candidate trade, broker place_order is executed and entry_context contains AI metadata."""
        ai_engine = DummyAIEngine(approved=True, confidence=0.88)
        use_case = EvaluateEntryUseCase(self.broker, self.event_bus, ai_engine=ai_engine)

        bars_m30, bars_m3, bars_m1 = self._build_test_bars()
        trade = self.loop.run_until_complete(use_case.execute(
            self.session_config, bars_m30, bars_m3, bars_m1, []
        ))

        # Check that AI engine was invoked
        self.assertEqual(ai_engine.call_count, 1)
        self.assertIsNotNone(trade)
        self.assertIsInstance(trade, TradeLifecycle)
        self.assertIn("ai_pre_evaluation", trade.entry_context)
        ai_meta = trade.entry_context["ai_pre_evaluation"]
        self.assertTrue(ai_meta["approved"])
        self.assertEqual(ai_meta["confidence"], 0.88)
        self.assertEqual(ai_meta["model_name"], "dummy-tester")

        # Telemetry events must have AI_PRE_ENTRY_EVALUATING and AI_ENTRY_APPROVED
        event_types = [e.get("type") for e in self.published_events]
        self.assertIn("AI_PRE_ENTRY_EVALUATING", event_types)
        self.assertIn("AI_ENTRY_APPROVED", event_types)

    def test_pre_entry_ai_vetoed_flow(self):
        """When AI vetoes candidate trade, order must NOT be sent to broker and None is returned."""
        ai_engine = DummyAIEngine(approved=False, confidence=0.25)
        use_case = EvaluateEntryUseCase(self.broker, self.event_bus, ai_engine=ai_engine)

        bars_m30, bars_m3, bars_m1 = self._build_test_bars()
        initial_open_orders = len(self.broker.orders)

        trade = self.loop.run_until_complete(use_case.execute(
            self.session_config, bars_m30, bars_m3, bars_m1, []
        ))

        self.assertEqual(ai_engine.call_count, 1)
        self.assertIsNone(trade)
        self.assertEqual(len(self.broker.orders), initial_open_orders)

        # Telemetry must contain AI_ENTRY_VETOED
        event_types = [e.get("type") for e in self.published_events]
        self.assertIn("AI_ENTRY_VETOED", event_types)
        veto_event = next(e for e in self.published_events if e.get("type") == "AI_ENTRY_VETOED")
        self.assertIn("AI Veto Test", veto_event["reason"])

    def test_pre_entry_ai_low_confidence_vetoed(self):
        """When AI marks approved=True but confidence is below min_ai_confidence (0.65), it should be vetoed."""
        ai_engine = DummyAIEngine(approved=True, confidence=0.50) # below 0.65
        use_case = EvaluateEntryUseCase(self.broker, self.event_bus, ai_engine=ai_engine)

        bars_m30, bars_m3, bars_m1 = self._build_test_bars()
        trade = self.loop.run_until_complete(use_case.execute(
            self.session_config, bars_m30, bars_m3, bars_m1, []
        ))

        self.assertIsNone(trade)
        event_types = [e.get("type") for e in self.published_events]
        self.assertIn("AI_ENTRY_VETOED", event_types)

    def test_pre_entry_ai_disabled_bypasses_check(self):
        """When enable_ai_pre_entry is False in risk_management, AI is bypassed."""
        self.session_config.risk_management["enable_ai_pre_entry"] = False
        ai_engine = DummyAIEngine(approved=False, confidence=0.10) # Would reject if called
        use_case = EvaluateEntryUseCase(self.broker, self.event_bus, ai_engine=ai_engine)

        bars_m30, bars_m3, bars_m1 = self._build_test_bars()
        trade = self.loop.run_until_complete(use_case.execute(
            self.session_config, bars_m30, bars_m3, bars_m1, []
        ))

        # AI engine was not called
        self.assertEqual(ai_engine.call_count, 0)
        self.assertIsNotNone(trade)
        self.assertIsNone(trade.entry_context["ai_pre_evaluation"])

    def test_pre_entry_ai_timeout_reject_policy(self):
        """When AI evaluation times out (>4s) and policy is REJECT, entry is aborted."""
        # 4.5s delay to trigger timeout
        ai_engine = DummyAIEngine(approved=True, confidence=0.9, delay=4.5)
        self.session_config.execution_rules["ai_timeout_policy"] = "REJECT"
        use_case = EvaluateEntryUseCase(self.broker, self.event_bus, ai_engine=ai_engine)

        bars_m30, bars_m3, bars_m1 = self._build_test_bars()
        trade = self.loop.run_until_complete(use_case.execute(
            self.session_config, bars_m30, bars_m3, bars_m1, []
        ))

        self.assertIsNone(trade)
        event_types = [e.get("type") for e in self.published_events]
        self.assertIn("AI_ENTRY_VETOED", event_types)

    def test_server_a_risk_config_ai_gatekeeper_api(self):
        """Test Server A risk_config endpoints update enable_ai_pre_entry and min_ai_confidence."""
        from presentation.api.server_a_api import create_server_a_app, UpdateRiskConfigRequest
        from core.use_cases.execution.circuit_breaker import CircuitBreakerUseCase

        cb = CircuitBreakerUseCase(self.broker, self.event_bus)
        dummy_state = {
            "symbol": "XAUUSD",
            "session_config_obj": self.session_config,
            "active_trades": []
        }

        app = create_server_a_app(self.broker, self.event_bus, cb, dummy_state)
        get_risk_route = next(r for r in app.routes if getattr(r, "path", None) == "/api/risk_config" and "GET" in r.methods)
        post_risk_route = next(r for r in app.routes if getattr(r, "path", None) == "/api/risk_config" and "POST" in r.methods)

        # 1. GET risk config
        get_res = self.loop.run_until_complete(get_risk_route.endpoint())
        self.assertTrue(get_res["enable_ai_pre_entry"])
        self.assertEqual(get_res["min_ai_confidence"], 0.65)

        # 2. POST update risk config with modified AI gatekeeper values
        update_req = UpdateRiskConfigRequest(
            enable_ai_pre_entry=False,
            min_ai_confidence=0.75
        )
        post_res = self.loop.run_until_complete(post_risk_route.endpoint(update_req))
        self.assertEqual(post_res["status"], "SUCCESS")
        self.assertFalse(post_res["execution_rules"]["enable_ai_pre_entry"])
        self.assertEqual(post_res["execution_rules"]["min_ai_confidence"], 0.75)

        # 3. Verify changes reflected in GET
        refreshed_res = self.loop.run_until_complete(get_risk_route.endpoint())
        self.assertFalse(refreshed_res["enable_ai_pre_entry"])
        self.assertEqual(refreshed_res["min_ai_confidence"], 0.75)


if __name__ == "__main__":
    unittest.main()
