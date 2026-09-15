import unittest
import asyncio
import tempfile
import shutil
import os
from typing import List

from core.domain.models import (
    TradeLifecycle, SetupType, OrderSide, PositionState,
    PositionPart, SessionConfig, MarketRegime, Bar
)
from infrastructure.bus.async_event_bus import AsyncEventBus
from infrastructure.storage.memory_vector_store import MemoryVectorStore
from infrastructure.storage.json_store import LocalJsonStore
from infrastructure.ai.mock_ai_adapter import MockAIEngine
from core.use_cases.intelligence.post_trade_evaluator import PostTradeEvaluatorUseCase
from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
from infrastructure.brokers.paper_broker import PaperBroker

class TestPostTradeEvaluatorAndClosedLoop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.json_store = LocalJsonStore(self.temp_dir)
        self.vector_store = MemoryVectorStore()
        self.event_bus = AsyncEventBus()
        self.ai_engine = MockAIEngine()
        self.post_trade_evaluator = PostTradeEvaluatorUseCase(
            ai_engine=self.ai_engine,
            vector_store=self.vector_store,
            json_store=self.json_store,
            event_bus=self.event_bus
        )

        self.session_cfg = SessionConfig(
            session_id="test_ses_01",
            symbol="XAUUSD",
            generated_at="2026-09-14T08:00:00Z",
            market_regime=MarketRegime.TRENDING_STEADY,
            resistance_zones=[],
            support_zones=[],
            setups_enabled={"PB": True},
            execution_rules={},
            risk_management={"max_risk_usd": 100.0},
            news_filter={},
            session_tag="2026-09-14_LONDON"
        )

    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_stopped_out_trade_reflection_and_storage(self):
        """Verify stopped out trade triggers reflection, updates vector store and persists to json."""
        trade = TradeLifecycle(
            trade_id="trade_stop_01",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.STOPPED_OUT,
            part1=PositionPart(1, 0.1, 2640.0, 2638.0, 2644.0, pnl=-20.0),
            part2=PositionPart(2, 0.1, 2640.0, 2638.0, 2648.0, pnl=-20.0),
            open_time=1700000000.0,
            close_context={"reason": "Hit initial Stop Loss at 2638.0", "bars_in_trade": 3}
        )

        reflection = await self.post_trade_evaluator.evaluate_and_learn(
            trade=trade,
            session_cfg=self.session_cfg
        )

        self.assertIsNotNone(reflection)
        self.assertIn("rating", reflection)
        self.assertIn("lesson", reflection)
        self.assertEqual(reflection["rating"], "STOP_LOSS_HIT")

        # Verify Vector Store received lesson
        lessons = self.vector_store.get_session_lessons("2026-09-14_LONDON")
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0]["session_tag"], "2026-09-14_LONDON")

        # Verify disk persistence
        persisted = self.json_store.load_session_lessons()
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["session_tag"], "2026-09-14_LONDON")

    async def test_winning_trade_reflection(self):
        """Verify fully closed profitable trade reinforces correct execution."""
        trade = TradeLifecycle(
            trade_id="trade_win_01",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.FULLY_CLOSED,
            part1=PositionPart(1, 0.1, 2640.0, 2638.0, 2644.0, pnl=40.0),
            part2=PositionPart(2, 0.1, 2640.0, 2638.0, 2648.0, pnl=80.0),
            open_time=1700000000.0,
            close_context={"reason": "Both targets achieved", "bars_in_trade": 12}
        )

        reflection = await self.post_trade_evaluator.evaluate_and_learn(
            trade=trade,
            session_cfg=self.session_cfg
        )

        self.assertIsNotNone(reflection)
        self.assertEqual(reflection["rating"], "OPTIMAL_EXECUTION")
        self.assertIn("lesson", reflection)

    async def test_closed_loop_feedback_into_next_trade(self):
        """Verify lessons from past stopped out trades are fed into future candidate evaluations."""
        # 1. Simulate a loss
        trade1 = TradeLifecycle(
            trade_id="trade_fail_tight_sl",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.STOPPED_OUT,
            part1=PositionPart(1, 0.1, 2640.0, 2639.2, 2644.0, pnl=-8.0),
            part2=PositionPart(2, 0.1, 2640.0, 2639.2, 2648.0, pnl=-8.0),
            open_time=1700000000.0,
            close_context={"reason": "SL breached within 2 bars", "bars_in_trade": 2}
        )
        await self.post_trade_evaluator.evaluate_and_learn(trade1, self.session_cfg)

        # 2. Setup EvaluateEntryUseCase with vector_store
        broker = PaperBroker(initial_balance=10000.0, symbol="XAUUSD")
        evaluate_entry = EvaluateEntryUseCase(
            broker=broker,
            event_bus=self.event_bus,
            ai_engine=self.ai_engine,
            vector_store=self.vector_store
        )

        # Search lessons for candidate context
        relevant_lessons = await self.vector_store.search_lessons(
            query="PB BUY pullback",
            regime="TRENDING_STEADY",
            session_tag="2026-09-14_LONDON"
        )
        self.assertTrue(len(relevant_lessons) > 0)
        self.assertTrue(any("2026-09-14_LONDON" in l for l in relevant_lessons))

        # Evaluate candidate trade via Mock AI:
        # SL distance = 2.2 USD (between min_sl_points*0.8=2.0 and min_sl_points*1.1=2.75)
        candidate = {
            "symbol": "XAUUSD",
            "setup": "PB",
            "side": "BUY",
            "order_price": 2640.0,
            "sl": 2637.8,  # 2.2 pt distance
            "tp1": 2644.0,
            "trend": "UPTREND",
            "wholesale": {"is_wholesale": True, "depth_pct": 50.0},
            "session_lessons": relevant_lessons
        }
        res = await self.ai_engine.evaluate_candidate_trade(
            candidate_context=candidate,
            trading_config={"symbol": "XAUUSD", "market_regime": "TRENDING_STEADY"},
            recent_bars={}
        )
        # Should veto due to tight SL under recent session experience
        self.assertFalse(res.approved)
        self.assertIn("session record", res.reason)  # Structured record rejection message

if __name__ == "__main__":
    unittest.main()
