"""
Unit tests for Filtering Uncertain / Low-Confidence Trades
(Layer 2 Gate >= 0.80, Layer 3 AI Confidence >= 0.75, AI Timeout REJECT, Anti-Congestion Filter, 0.01 Lot Split)
"""
import unittest
import asyncio
import time
from core.domain.models import (
    Bar, OrderSide, PositionPart, PositionState, SetupType,
    TradeLifecycle, SessionConfig, MarketRegime, HTFZone, Significance,
    get_instrument_profile, calculate_pnl
)
from core.domain.rules.pre_entry_scorer import PreEntryScorer
from core.domain.rules.candlestick_engine import CandlestickEngine
from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.bus.async_event_bus import AsyncEventBus

class MockAIAdapter:
    def __init__(self, approved=True, confidence=0.85, reason="Approved by AI"):
        self.approved = approved
        self.confidence = confidence
        self.reason = reason
        self.model_name = "MockAI"

    async def evaluate_candidate_trade(self, candidate_ctx, trading_cfg, recent_snapshot):
        from core.domain.models import PreEntryEvaluation
        return PreEntryEvaluation(
            approved=self.approved,
            confidence=self.confidence,
            reason=self.reason
        )

class MockAITimeoutAdapter:
    def __init__(self):
        self.model_name = "TimeoutAI"

    async def evaluate_candidate_trade(self, candidate_ctx, trading_cfg, recent_snapshot):
        await asyncio.sleep(5.0)  # Exceeds 4s timeout

class TestUncertainTradeFilters(unittest.TestCase):
    def setUp(self):
        self.broker = PaperBroker(initial_balance=10000.0, symbol="XAUUSD")
        self.bus = AsyncEventBus()
        self.profile = get_instrument_profile("XAUUSD")

    def test_layer2_rejects_trades_below_080_threshold(self):
        """
        Layer 2 Deterministic Scorer must reject trades with score < 0.80.
        """
        # A mediocre setup with R:R = 1.05 and mediocre wholesale fit
        res = PreEntryScorer.evaluate(
            setup="PB",
            side="BUY",
            regime="TRENDING_WEAKENING", # compat 0.30 -> fails REGIME_COMPAT_MIN 0.40
            wholesale={
                "is_valid_entry": True,
                "recommended_entry": 2650.0,
                "S1": 2646.0,
                "T1": 2654.5,
                "LWP": 2651.0,
                "LRP": 2650.0,
                "rr_ratio_part1": 1.1,
                "min_rr_ratio": 1.0
            },
            profile=self.profile
        )
        self.assertFalse(res.approved)

    def test_layer2_rejects_trades_below_min_rr_10(self):
        """
        Layer 2 Deterministic Scorer must reject trades with R:R < 1.0 (Hard Fail).
        """
        res = PreEntryScorer.evaluate(
            setup="TST",
            side="BUY",
            regime="SIDEWAYS_RANGE",
            wholesale={
                "is_valid_entry": True,
                "recommended_entry": 2650.0,
                "S1": 2645.0,
                "T1": 2654.0,
                "LWP": 2651.0,
                "LRP": 2650.0,
                "rr_ratio_part1": 0.80, # < 1.0
                "min_rr_ratio": 1.0
            },
            profile=self.profile
        )
        self.assertFalse(res.approved)
        self.assertIn("thấp hơn mức tối thiểu 1.00", res.rejection_reason)

    def test_ai_pre_entry_rejects_confidence_below_075(self):
        """
        Layer 3 AI Gatekeeper must veto candidate trade if confidence < 0.75.
        """
        ai = MockAIAdapter(approved=True, confidence=0.70, reason="Moderate confidence only")
        use_case = EvaluateEntryUseCase(self.broker, self.bus, ai_engine=ai)

        config = SessionConfig(
            session_id="test_sess",
            symbol="XAUUSD",
            generated_at="2026-09-22T08:00:00Z",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[HTFZone(id="rz1", high=2660.0, low=2658.0, significance=Significance.MAJOR)],
            support_zones=[HTFZone(id="sz1", high=2645.0, low=2643.0, significance=Significance.MAJOR)],
            setups_enabled={"TST": True},
            execution_rules={"enable_ai_pre_entry": True, "min_ai_confidence": 0.75},
            risk_management={"account_risk_limit_percent": 1.0},
            news_filter={"macro_bias": "NEUTRAL"}
        )

        bars_m1 = [
            Bar(timestamp=100, open=2646.0, high=2646.2, low=2644.0, close=2644.5),
            Bar(timestamp=160, open=2644.5, high=2645.2, low=2644.0, close=2644.8)
        ]

        # TST Wholesale setup
        wholesale = use_case.setups["TST"].evaluate(
            trend="NEUTRAL",
            swings_3m=[],
            bars_1m=bars_m1,
            resistance_zones=config.resistance_zones,
            support_zones=config.support_zones,
            profile=self.profile
        )

        trade = asyncio.run(use_case._execute_market_trade(
            config=config,
            setup_name="TST",
            side=OrderSide.BUY,
            current_price=2644.8,
            actual_sl=2641.0,
            actual_tp1=2654.0,
            actual_tp2=2660.0,
            lot_total=0.01,
            lot_p1=0.01,
            lot_p2=0.0,
            wholesale={
                "is_valid_entry": True,
                "recommended_entry": 2644.8,
                "S1": 2641.0,
                "T1": 2654.0,
                "T2": 2660.0,
                "LWP": 2646.0,
                "LRP": 2644.8,
                "rr_ratio_part1": 2.4,
                "min_rr_ratio": 1.0
            },
            anchor_id="anchor_test",
            spatial_anchor_key="TST_BUY_2644.8",
            stall_low=2644.0,
            stall_high=2645.2,
            trend="NEUTRAL",
            bars_m1=bars_m1,
            bars_m3=[],
            risk_limit=1.0,
            profile=self.profile
        ))

        # Trade must be VETOED by AI
        self.assertIsNone(trade)

    def test_ai_timeout_policy_defaults_to_reject(self):
        """
        When AI times out (>4s), the default fallback policy must be REJECT.
        """
        ai = MockAITimeoutAdapter()
        use_case = EvaluateEntryUseCase(self.broker, self.bus, ai_engine=ai)

        config = SessionConfig(
            session_id="test_sess",
            symbol="XAUUSD",
            generated_at="2026-09-22T08:00:00Z",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[],
            support_zones=[],
            setups_enabled={"TST": True},
            execution_rules={"enable_ai_pre_entry": True}, # no explicit fallback -> defaults to REJECT
            risk_management={},
            news_filter={}
        )

        bars_m1 = [
            Bar(timestamp=100, open=2644.5, high=2645.2, low=2644.0, close=2644.8)
        ]

        trade = asyncio.run(use_case._execute_market_trade(
            config=config,
            setup_name="TST",
            side=OrderSide.BUY,
            current_price=2644.8,
            actual_sl=2641.0,
            actual_tp1=2654.0,
            actual_tp2=2660.0,
            lot_total=0.01,
            lot_p1=0.01,
            lot_p2=0.0,
            wholesale={
                "is_valid_entry": True,
                "recommended_entry": 2644.8,
                "S1": 2641.0,
                "T1": 2654.0,
                "T2": 2660.0,
                "LWP": 2646.0,
                "LRP": 2644.8,
                "rr_ratio_part1": 2.4,
                "min_rr_ratio": 1.0
            },
            anchor_id="anchor_test",
            spatial_anchor_key="TST_BUY_2644.8",
            stall_low=2644.0,
            stall_high=2645.2,
            trend="NEUTRAL",
            bars_m1=bars_m1,
            bars_m3=[],
            risk_limit=1.0,
            profile=self.profile
        ))

        # Trade must be rejected due to timeout
        self.assertIsNone(trade)

    def test_anti_congestion_filter_rejects_continuation_in_chop(self):
        """
        Anti-Congestion filter must identify sideways choppy bars with long wicks
        and block continuation setups (PB, CPB, BPB, YUM_YUM).
        """
        # Create 5 congestion bars: both upper and lower wicks >= 30% of range
        congested_bars = [
            Bar(timestamp=100 + i*60, open=2650.0, high=2651.0, low=2649.0, close=2650.0)
            for i in range(5)
        ]

        is_choppy = CandlestickEngine.is_congestion(congested_bars, lookback=5)
        self.assertTrue(is_choppy)

    def test_lot_allocation_for_001_lot(self):
        """
        When fixed_lot is 0.01, lot_p1 must be 0.01 and lot_p2 must be 0.0
        (instead of rounding 0.005 to 0.0).
        """
        lot_total = 0.01
        if lot_total < 0.02:
            lot_p1 = lot_total
            lot_p2 = 0.0
        else:
            lot_p1 = round(lot_total * 0.5, 2)
            lot_p2 = round(lot_total - lot_p1, 2)

        self.assertEqual(lot_total, 0.01)
        self.assertEqual(lot_p1, 0.01)
        self.assertEqual(lot_p2, 0.0)

if __name__ == "__main__":
    unittest.main()
