import unittest
import asyncio
import time
from core.domain.models import (
    Bar, SessionConfig, HTFZone, MarketRegime, SetupType, OrderSide, SwingNode, SwingType,
    get_instrument_profile
)
from core.domain.rules.wholesale_engine import WholesaleEngine
from core.domain.rules.setups.setups import PBSetup, CPBSetup
from core.domain.rules.setups.base import BaseSetup
from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.bus.async_event_bus import AsyncEventBus
from infrastructure.ai.mock_ai_adapter import MockAIEngine

class TestAdaptiveEntryAndMicroSL(unittest.TestCase):
    def setUp(self):
        self.profile = get_instrument_profile("XAUUSD")

    def test_adaptive_wholesale_entry_calculation(self):
        """Tests that adaptive_entry=True yields entry within 40% retrace of stall while preserving R:R."""
        # Setup: Buy stall [2640.0, 2645.0], T1=2655.0, Swing=2635.0
        ws_strict = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.INSIDE_BAR_SMA21,
            side=OrderSide.BUY,
            pullback_swing_price=2635.0,
            t1_price=2655.0,
            t2_price=2665.0,
            micro_stall_high=2645.0,
            micro_stall_low=2640.0,
            buffer_pts=0.80,
            adaptive_entry=False
        )
        self.assertEqual(ws_strict.recommended_entry, 2640.0)

        ws_adaptive = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.INSIDE_BAR_SMA21,
            side=OrderSide.BUY,
            pullback_swing_price=2635.0,
            t1_price=2655.0,
            t2_price=2665.0,
            micro_stall_high=2645.0,
            micro_stall_low=2640.0,
            buffer_pts=0.80,
            adaptive_entry=True
        )
        # 2640.0 + (2645.0 - 2640.0) * 0.40 = 2642.0
        self.assertEqual(ws_adaptive.recommended_entry, 2642.0)
        self.assertTrue(ws_adaptive.is_valid_entry)
        self.assertGreaterEqual(ws_adaptive.rr_ratio_part1, 1.0)

    def test_pb_micro_structure_sl_when_swing_is_distant(self):
        """Tests that PBSetup anchors to local M1 micro swing when M3 swing is too distant."""
        pb = PBSetup()
        # M3 swing is at 2620.0 (28 points away from curr_price 2648.0)
        dummy_bar = Bar(timestamp=100, open=2615.0, high=2616.0, low=2614.0, close=2615.0, volume=100, timeframe="M3")
        swings_3m = [
            SwingNode(swing_type=SwingType.SWING_LOW, price=2615.0, time=100, bar_index=1, bar=dummy_bar),
            SwingNode(swing_type=SwingType.SWING_HIGH, price=2640.0, time=200, bar_index=2, bar=dummy_bar),
            SwingNode(swing_type=SwingType.SWING_LOW, price=2620.0, time=300, bar_index=3, bar=dummy_bar)
        ]
        # Build 15 M1 bars where recent micro low is at 2644.0
        bars_1m = []
        for i in range(15):
            low_p = 2644.0 if i == 10 else (2645.0 + i * 0.2)
            bars_1m.append(Bar(
                timestamp=float(300 + i * 60),
                open=2646.0,
                high=2649.0,
                low=low_p,
                close=2648.0,
                volume=100,
                timeframe="M1"
            ))

        trig, side, pullback_price, t1, t2 = pb.evaluate(
            trend="UPTREND",
            swings_3m=swings_3m,
            bars_1m=bars_1m,
            resistance_zones=[],
            support_zones=[],
            profile=self.profile
        )

        self.assertTrue(trig)
        self.assertEqual(side, OrderSide.BUY)
        # Instead of 2620.0 (macro swing), pullback_price must be clamped to micro_low 2644.0
        self.assertEqual(pullback_price, 2644.0)

    def test_trend_invalidation_allows_trend_flip_trades(self):
        """Tests that UPTREND_INVALIDATED does not block SELL setups in BaseSetup.is_setup_compatible."""
        is_compat, msg = BaseSetup.is_setup_compatible(
            setup_type=SetupType.BPB,
            side=OrderSide.SELL,
            market_regime=MarketRegime.TRENDING_STEADY,
            trend="UPTREND_INVALIDATED"
        )
        self.assertTrue(is_compat)
        self.assertEqual(msg, "COMPATIBLE")

        # Active UPTREND still blocks counter-trend SELL
        is_compat_up, _ = BaseSetup.is_setup_compatible(
            setup_type=SetupType.BPB,
            side=OrderSide.SELL,
            market_regime=MarketRegime.TRENDING_STEADY,
            trend="UPTREND"
        )
        self.assertFalse(is_compat_up)

    def test_bof_allowed_during_breakout_expansion(self):
        """Tests that BOF (liquidity trap / spring) is permitted during BREAKOUT_EXPANSION."""
        is_compat, msg = BaseSetup.is_setup_compatible(
            setup_type=SetupType.BOF,
            side=OrderSide.BUY,
            market_regime=MarketRegime.BREAKOUT_EXPANSION,
            trend="SIDEWAYS"
        )
        self.assertTrue(is_compat)
        self.assertEqual(msg, "COMPATIBLE")

if __name__ == "__main__":
    unittest.main()
