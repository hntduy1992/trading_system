"""
Unit tests verifying the 5 algorithmic defense solutions for YTC Trading System
"""
import unittest
import time
import asyncio
from core.domain.models import (
    Bar, SwingNode, SetupType, OrderSide, PositionState, PositionPart,
    TradeLifecycle, MarketRegime, SessionConfig, HTFZone, Significance, get_instrument_profile
)
from core.domain.rules.setups.base import BaseSetup
from core.domain.rules.wholesale_engine import WholesaleEngine
from core.domain.rules.risk_manager import RiskManager
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.bus.async_event_bus import AsyncEventBus
from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase

class TestAlgorithmicSolutions(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.event_bus = AsyncEventBus()
        self.broker = PaperBroker(initial_balance=10000.0, symbol="XAUUSD")
        self.evaluate_entry = EvaluateEntryUseCase(self.broker, self.event_bus)
        self.manage_lifecycle = ManageLifecycleUseCase(self.broker, self.event_bus)

    def tearDown(self):
        self.loop.close()

    def test_1_regime_and_trend_gating_matrix(self):
        """Solution 1: Matrix must prohibit counter-trend TST/BOF in Trending/Expansion regimes."""
        # 1. Breakout Expansion + Uptrend -> Prohibit BOF SELL and TST SELL
        ok, msg = BaseSetup.is_setup_compatible(SetupType.BOF, OrderSide.SELL, MarketRegime.BREAKOUT_EXPANSION, "UPTREND")
        self.assertFalse(ok)
        self.assertIn("strictly forbidden", msg)

        ok, msg = BaseSetup.is_setup_compatible(SetupType.TST, OrderSide.SELL, MarketRegime.BREAKOUT_EXPANSION, "UPTREND")
        self.assertFalse(ok)

        # 2. Trending Steady + Downtrend -> Prohibit BOF BUY and TST BUY
        ok, msg = BaseSetup.is_setup_compatible(SetupType.BOF, OrderSide.BUY, MarketRegime.TRENDING_STEADY, "DOWNTREND")
        self.assertFalse(ok)

        # 3. Trending Steady + Uptrend -> Allow PB BUY and BPB BUY
        ok, msg = BaseSetup.is_setup_compatible(SetupType.PB, OrderSide.BUY, MarketRegime.TRENDING_STEADY, "UPTREND")
        self.assertTrue(ok)
        self.assertEqual(msg, "COMPATIBLE")

        # 4. Sideways Range -> Allow TST BUY / SELL
        ok, msg = BaseSetup.is_setup_compatible(SetupType.TST, OrderSide.BUY, MarketRegime.SIDEWAYS_RANGE, "SIDEWAYS")
        self.assertTrue(ok)

    def test_2_dynamic_sl_floor_and_rr_invalidation(self):
        """Solution 2: WholesaleEngine must enforce minimum SL floor (>= 2.50 USD for Gold)."""
        min_sl_floor = 2.50
        calc = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.PB,
            side=OrderSide.SELL,
            pullback_swing_price=2650.5,
            t1_price=2646.0,
            t2_price=2635.0,
            micro_stall_high=2650.0,
            micro_stall_low=2649.0,
            buffer_pts=0.80,
            min_sl_distance=min_sl_floor
        )

        # SL should be clamped to at least 2650.0 + 2.50 = 2652.50
        self.assertGreaterEqual(calc.S1, 2652.50)
        risk_dist = calc.S1 - calc.recommended_entry
        self.assertGreaterEqual(risk_dist, min_sl_floor)
        self.assertTrue(calc.is_valid_entry)

        # Poor R:R invalidation
        calc_poor_rr = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.PB,
            side=OrderSide.SELL,
            pullback_swing_price=2650.5,
            t1_price=2648.5,
            t2_price=2635.0,
            micro_stall_high=2650.0,
            micro_stall_low=2649.0,
            buffer_pts=0.80,
            min_sl_distance=min_sl_floor
        )
        self.assertFalse(calc_poor_rr.is_valid_entry)

    def test_3_anti_revenge_and_stopped_out_anchor_lockout(self):
        """Solution 4: Stopped-out anchors and spatial keys must be permanently blacklisted."""
        trade = TradeLifecycle(
            trade_id="trade_stopout_01",
            symbol="XAUUSD",
            setup_type=SetupType.TST,
            side=OrderSide.SELL,
            state=PositionState.STOPPED_OUT,
            part1=PositionPart(1, 0.01, 2650.0, 2653.0, 2640.0),
            part2=PositionPart(2, 0.01, 2650.0, 2653.0, 2630.0),
            open_time=time.time(),
            anchor_id="TST_SELL_2650.0_12345",
            spatial_anchor_key="TST_SELL_2650.0"
        )
        trade.close_time = time.time()
        self.evaluate_entry.record_trade_closed(trade, is_loss=True)

        self.assertIn("TST_SELL_2650.0_12345", self.evaluate_entry.stopped_out_anchors)
        self.assertIn("TST_SELL_2650.0", self.evaluate_entry.stopped_out_spatial_keys)
        self.assertGreater(self.evaluate_entry.last_stopped_out_time, 0.0)

    def test_4_two_tier_scratch_grace_period_and_m3_structure(self):
        """Solution 3: Scratch rule must protect first 4 bars and require M3 confirmation."""
        # Grace period active
        is_scratch, reason = RiskManager.evaluate_scratch_rule(
            bars_in_trade=2,
            scratch_timeout_bars=5,
            grace_period_bars=4,
            opposite_momentum_detected=False,
            m3_structure_broken=False
        )
        self.assertFalse(is_scratch)
        self.assertIn("Grace Period Active", reason)

        # Timeout after grace period
        is_scratch, reason = RiskManager.evaluate_scratch_rule(
            bars_in_trade=6,
            scratch_timeout_bars=5,
            grace_period_bars=4,
            opposite_momentum_detected=False,
            m3_structure_broken=False
        )
        self.assertTrue(is_scratch)
        self.assertIn("Scratch timeout reached", reason)

        # Structural scratch
        is_scratch, reason = RiskManager.evaluate_scratch_rule(
            bars_in_trade=1,
            scratch_timeout_bars=5,
            grace_period_bars=4,
            opposite_momentum_detected=False,
            m3_structure_broken=True
        )
        self.assertTrue(is_scratch)
        self.assertIn("M3 structural swing violated", reason)

    def test_5_instrument_profile_parameters(self):
        """Profile must define safe SL floor and spread tolerance for XAUUSD."""
        profile = get_instrument_profile("XAUUSD")
        self.assertGreaterEqual(profile.min_sl_points, 2.50)
        self.assertLessEqual(profile.max_spread_points, 0.50)

if __name__ == '__main__':
    unittest.main()
