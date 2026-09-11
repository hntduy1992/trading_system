"""
Unit tests for Post-Session Hindsight Audit with Trade Context and Plan Critique
"""
import unittest
import asyncio
import time
from core.domain.models import (
    TradeLifecycle, PositionPart, PositionState, SetupType, OrderSide,
    SessionConfig, MarketRegime, HTFZone, Significance
)
from core.use_cases.intelligence.hindsight_auditor import HindsightAuditorUseCase
from infrastructure.ai.mock_ai_adapter import MockAIEngine
from infrastructure.storage.memory_vector_store import MemoryVectorStore
from presentation.api.server_a_api import serialize_trade

class TestHindsightAudit(unittest.TestCase):
    def setUp(self):
        self.ai_engine = MockAIEngine()
        self.vector_store = MemoryVectorStore()
        self.auditor = HindsightAuditorUseCase(self.ai_engine, self.vector_store)

        self.trading_config = {
            "session_id": "sess_audit_test",
            "symbol": "XAUUSD",
            "market_regime": "SIDEWAYS_RANGE",
            "setups_enabled": {"TST": True, "BOF": True, "PB": False, "CPB": False, "BPB": False},
            "htf_zones": {
                "resistance_zones": [{"id": "res_1", "high": 2655.0, "low": 2653.0}],
                "support_zones": [{"id": "sup_1", "high": 2642.0, "low": 2640.0}]
            },
            "execution_rules": {"min_rr_ratio_part1": 1.0},
            "risk_management": {"account_risk_limit_percent": 1.0}
        }

    def test_trade_lifecycle_context_fields(self):
        """Test TradeLifecycle holds entry_context and close_context correctly."""
        trade = TradeLifecycle(
            trade_id="trade_test_01",
            symbol="XAUUSD",
            setup_type=SetupType.TST,
            side=OrderSide.BUY,
            state=PositionState.FULLY_CLOSED,
            part1=PositionPart(1, 0.2, 2641.0, 2639.0, 2646.0, ticket=101),
            part2=PositionPart(2, 0.2, 2641.0, 2639.0, 2654.0, ticket=101),
            open_time=time.time(),
            entry_context={
                "symbol": "XAUUSD",
                "market_regime": "SIDEWAYS_RANGE",
                "setup": "TST",
                "side": "BUY",
                "order_price": 2641.0,
                "sl": 2639.0,
                "tp1": 2646.0,
                "wholesale": {"LWP": 2642.0, "LRP": 2640.0, "is_valid_entry": True, "rr_ratio_part1": 2.5}
            },
            close_context={
                "close_state": "FULLY_CLOSED",
                "close_reason": "T2_TARGET_HIT",
                "bars_in_trade": 5
            }
        )

        serialized = serialize_trade(trade)
        self.assertEqual(serialized["trade_id"], "trade_test_01")
        self.assertEqual(serialized["entry_context"]["setup"], "TST")
        self.assertEqual(serialized["close_context"]["close_reason"], "T2_TARGET_HIT")

    def test_hindsight_auditor_execution_and_plan_critique(self):
        """Test HindsightAuditorUseCase evaluates trades and critiques plan."""
        trades = [
            {
                "trade_id": "ytc_01",
                "setup": "TST",
                "side": "BUY",
                "state": "FULLY_CLOSED",
                "entry_context": {
                    "setup": "TST",
                    "order_price": 2641.0,
                    "sl": 2639.0,
                    "tp1": 2646.0,
                    "wholesale": {"is_valid_entry": True, "LWP": 2642.0, "LRP": 2640.0}
                },
                "close_context": {
                    "close_state": "FULLY_CLOSED",
                    "close_reason": "T2_TARGET_HIT"
                }
            },
            {
                "trade_id": "ytc_02",
                "setup": "PB",
                "side": "SELL",
                "state": "STOPPED_OUT",
                "entry_context": {
                    "setup": "PB",
                    "order_price": 2645.0,
                    "sl": 2647.0,
                    "tp1": 2640.0,
                    "wholesale": {"is_valid_entry": False, "LWP": 2644.0, "LRP": 2648.0}
                },
                "close_context": {
                    "close_state": "STOPPED_OUT",
                    "close_reason": "INITIAL_STOP_LOSS_HIT"
                }
            }
        ]

        report = asyncio.run(self.auditor.execute(
            trading_config_used=self.trading_config,
            session_trades_json=trades,
            full_session_ohlcv={}
        ))

        self.assertLess(report.compliance_score, 1.0)
        self.assertGreater(len(report.rule_violations), 0)
        self.assertIsNotNone(report.plan_critique)
        self.assertIn("regime_accuracy", report.plan_critique)
        self.assertIn("wholesale_engine_assessment", report.plan_critique)
        self.assertEqual(len(report.trade_evaluations), 2)
        self.assertEqual(report.trade_evaluations[0]["score"], 1.0)
        self.assertLess(report.trade_evaluations[1]["score"], 1.0)
        self.assertIn("scratch_timeout_bars_1m", report.parameter_adjustments_suggested)
        self.assertGreater(len(report.lessons_learned), 0)

    def test_server_a_closed_trades_history(self):
        """Test Server A closed_trades tracking and serialization."""
        from presentation.api.server_a_api import create_server_a_app
        from infrastructure.brokers.paper_broker import PaperBroker
        from infrastructure.bus.async_event_bus import AsyncEventBus
        from core.use_cases.execution.circuit_breaker import CircuitBreakerUseCase

        broker = PaperBroker(initial_balance=10000.0)
        bus = AsyncEventBus()
        cb = CircuitBreakerUseCase(broker, bus)
        dummy_state = {
            "symbol": "XAUUSD",
            "active_trades": [],
            "closed_trades": [
                TradeLifecycle(
                    trade_id="hist_01",
                    symbol="XAUUSD",
                    setup_type=SetupType.BOF,
                    side=OrderSide.SELL,
                    state=PositionState.FULLY_CLOSED,
                    part1=PositionPart(1, 0.1, 2650.0, 2652.0, 2645.0),
                    part2=PositionPart(2, 0.1, 2650.0, 2652.0, 2640.0),
                    open_time=1000.0,
                    close_time=1200.0,
                    entry_context={"setup": "BOF", "order_price": 2650.0},
                    close_context={"close_state": "FULLY_CLOSED", "close_reason": "T2_TARGET_HIT"}
                )
            ]
        }

        app = create_server_a_app(broker, bus, cb, dummy_state)
        # Find endpoint function for /api/trades/history
        history_route = [r for r in app.routes if getattr(r, "path", None) == "/api/trades/history"]
        self.assertEqual(len(history_route), 1)

        endpoint_fn = history_route[0].endpoint
        res = asyncio.run(endpoint_fn())
        self.assertEqual(res["total_closed"], 1)
        self.assertEqual(res["closed_trades"][0]["trade_id"], "hist_01")
        self.assertEqual(res["closed_trades"][0]["entry_context"]["setup"], "BOF")

    def test_server_b_audit_persistence(self):
        """Test Server B audit execution and persistence to JSON store."""
        import tempfile
        import os
        from presentation.api.server_b_api import create_server_b_app, AuditRequest
        from core.use_cases.intelligence.pre_session_planner import PreSessionPlannerUseCase
        from infrastructure.storage.json_store import LocalJsonStore
        from infrastructure.brokers.paper_broker import PaperBroker

        with tempfile.TemporaryDirectory() as tmpdir:
            json_store = LocalJsonStore(tmpdir)
            broker = PaperBroker(symbol="XAUUSD")
            planner = PreSessionPlannerUseCase(self.ai_engine, self.vector_store, broker)
            app = create_server_b_app(planner, self.auditor, self.vector_store, json_store)

            # Find audit route
            audit_route = [r for r in app.routes if getattr(r, "path", None) == "/api/audit" and "POST" in r.methods]
            self.assertEqual(len(audit_route), 1)

            req = AuditRequest(
                trading_config=self.trading_config,
                session_trades=[],
                full_session_ohlcv={}
            )
            audit_res = asyncio.run(audit_route[0].endpoint(req))
            self.assertEqual(audit_res["status"], "SUCCESS")

            # Check that file was saved to disk
            report_file = os.path.join(tmpdir, "last_audit_report.json")
            self.assertTrue(os.path.exists(report_file))

            # Check GET /api/audit/latest
            latest_route = [r for r in app.routes if getattr(r, "path", None) == "/api/audit/latest"]
            self.assertEqual(len(latest_route), 1)
            latest_res = asyncio.run(latest_route[0].endpoint())
            self.assertEqual(latest_res["status"], "SUCCESS")
            self.assertEqual(latest_res["audit_report"]["session_id"], "sess_audit_test")

if __name__ == "__main__":
    unittest.main()
