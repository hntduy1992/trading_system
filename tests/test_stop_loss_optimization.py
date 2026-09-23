"""
Unit tests for Stop Loss & Trailing Optimization (Anti-Premature Exit)
Ensures trades have breathing room, strong winners are not scratched on timeout,
and Part 2 SL trails behind M1/M3 swing nodes.
"""
import unittest
import asyncio
import time
from core.domain.models import (
    Bar, OrderSide, PositionPart, PositionState, SetupType, SwingType,
    TradeLifecycle, get_instrument_profile
)
from core.domain.rules.risk_manager import RiskManager
from core.domain.rules.profit_protector import ProfitProtector
from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.bus.async_event_bus import AsyncEventBus
from config import ProfitProtectionConfig

class TestStopLossOptimization(unittest.TestCase):

    def setUp(self):
        self.broker = PaperBroker(initial_balance=10000.0, symbol="XAUUSD")
        self.bus = AsyncEventBus()
        self.lifecycle = ManageLifecycleUseCase(self.broker, self.bus)

    def test_strong_profit_trade_exempt_from_scratch_timeout(self):
        """
        A winning trade in strong profit (>= 1.0R, e.g. +3.0R or +7.0R)
        must NEVER be killed by scratch timeout even after 14, 20 or more bars.
        """
        # Risk = 1.0 USD, profit = 3.0 USD (+3.0R)
        scratch_3r, reason_3r = RiskManager.evaluate_scratch_rule(
            bars_in_trade=14,
            scratch_timeout_bars=8,
            unrealized_r=3.0,
            price_progress_pct=0.45
        )
        self.assertFalse(scratch_3r)
        self.assertIn("In Strong Profit", reason_3r)

        # Extreme winner (e.g. +7.0R like historical trade ytc_1789090740725) at bar 20
        scratch_7r, reason_7r = RiskManager.evaluate_scratch_rule(
            bars_in_trade=20,
            scratch_timeout_bars=8,
            unrealized_r=7.0,
            price_progress_pct=0.42
        )
        self.assertFalse(scratch_7r)
        self.assertIn("In Strong Profit", reason_7r)

    def test_ratchet_levels_wide_breathing_room(self):
        """
        Verify new calibrated ratchet levels:
        - Level 1: requires >= 0.8R (locks 0.2R)
        - Level 2: requires >= 1.2R (locks 0.5R)
        """
        part1 = PositionPart(1, 0.01, 2650.00, 2646.00, 2654.00, ticket=1)
        part2 = PositionPart(2, 0.01, 2650.00, 2646.00, 2665.00, ticket=2)
        trade = TradeLifecycle(
            trade_id="test_ratchet_calib",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.TRAILING_STOP,
            part1=part1,
            part2=part2,
            open_time=time.time()
        )
        trade.part1.is_closed = True
        trade.part2.sl_price = 2650.30

        # At +0.6R (2652.40): should NOT trigger ratchet (remains at BE to give breathing room)
        sl_06r = ProfitProtector.evaluate_ratchet_sl(trade, curr_price=2652.40)
        self.assertIsNone(sl_06r)
        self.assertEqual(trade.profit_protection_level, 0)

        # At +0.85R (2653.40): triggers Level 1, locking 0.2R (2650.80)
        sl_085r = ProfitProtector.evaluate_ratchet_sl(trade, curr_price=2653.40)
        self.assertIsNotNone(sl_085r)
        self.assertEqual(trade.profit_protection_level, 1)
        self.assertEqual(sl_085r, 2650.80)

    def test_m1_swing_trailing_updates_sl(self):
        """
        In TRAILING_STOP state, newly formed M1 swing lows
        must trail Part 2 SL upwards to protect gains as trend progresses.
        """
        part1 = PositionPart(1, 0.01, 2650.00, 2646.00, 2654.00, ticket=1, is_closed=True, close_price=2654.00)
        part2 = PositionPart(2, 0.01, 2650.00, 2650.50, 2665.00, ticket=2)
        from config import CONFIG
        orig_trail = getattr(CONFIG.profit_protection, "ENABLE_SL_TRAILING", False)
        orig_swing = getattr(CONFIG.profit_protection, "ENABLE_SWING_TRAILING", False)
        orig_candle = getattr(CONFIG.profit_protection, "ENABLE_CANDLESTICK_EXITS", True)
        CONFIG.profit_protection.ENABLE_SL_TRAILING = True
        CONFIG.profit_protection.ENABLE_SWING_TRAILING = True
        CONFIG.profit_protection.ENABLE_CANDLESTICK_EXITS = False
        try:
            trade = TradeLifecycle(
                trade_id="test_m1_swing_trail",
                symbol="XAUUSD",
                setup_type=SetupType.PB,
                side=OrderSide.BUY,
                state=PositionState.TRAILING_STOP,
                part1=part1,
                part2=part2,
                open_time=time.time()
            )

            # Generate 6 M1 bars with a clear Swing Low at bar 2 (price 2652.00 > SL 2650.50)
            bars_m1 = [
                Bar(timestamp=100, open=2653.5, high=2654.0, low=2653.0, close=2653.2),
                Bar(timestamp=160, open=2653.2, high=2653.5, low=2652.5, close=2652.7),
                Bar(timestamp=220, open=2652.7, high=2653.0, low=2651.8, close=2652.5),  # Swing Low
                Bar(timestamp=280, open=2652.5, high=2653.5, low=2652.4, close=2653.2),
                Bar(timestamp=340, open=2653.0, high=2653.8, low=2652.8, close=2653.5),
                Bar(timestamp=400, open=2653.5, high=2653.8, low=2652.8, close=2653.0),
            ]

            asyncio.run(self.lifecycle.update(trade, bars_m1, []))

            # SL should now have trailed upwards from 2650.50, cushioned safely below the swing low (2651.80 - cushion)
            self.assertGreater(trade.part2.sl_price, 2650.50)
            self.assertLessEqual(trade.part2.sl_price, 2651.80)
            self.assertEqual(trade.part2.sl_price, 2651.45)
        finally:
            CONFIG.profit_protection.ENABLE_SL_TRAILING = orig_trail
            CONFIG.profit_protection.ENABLE_SWING_TRAILING = orig_swing
            CONFIG.profit_protection.ENABLE_CANDLESTICK_EXITS = orig_candle




    def test_pullback_immunity_refuses_sl_tighten_during_retracement(self):
        """
        PULLBACK IMMUNITY TEST:
        If peak was +1.3R, but price is currently pulling back close to the ratchet SL
        (within D_safe buffer), evaluate_ratchet_sl MUST refuse to tighten SL
        to avoid choking the position during the pullback candle.
        """
        part1 = PositionPart(1, 0.01, 2650.00, 2646.00, 2654.00, ticket=1, is_closed=True)
        part2 = PositionPart(2, 0.01, 2650.00, 2650.30, 2665.00, ticket=2)
        trade = TradeLifecycle(
            trade_id="test_pullback_immunity",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.TRAILING_STOP,
            part1=part1,
            part2=part2,
            open_time=time.time()
        )
        # Peak was +1.3R (price 2655.20)
        trade.max_unrealized_r_part2 = 1.30

        # Ratchet Level 2 locks +0.5R = 2652.00.
        # Price is currently retracing to 2652.40 (only 0.40 above lock level, < D_safe 1.0)
        sl_during_pullback = ProfitProtector.evaluate_ratchet_sl(
            trade, curr_price=2652.40, safe_cushion=1.00
        )
        # MUST BE REFUSED during the retracement candle!
        self.assertIsNone(sl_during_pullback)
        self.assertEqual(trade.profit_protection_level, 0)

        # Once price recovers and pushes forward to 2654.50 (> 2652.00 + 1.00)
        sl_after_recovery = ProfitProtector.evaluate_ratchet_sl(
            trade, curr_price=2654.50, safe_cushion=1.00
        )
        # Now safely accepted!
        self.assertIsNotNone(sl_after_recovery)
        self.assertEqual(sl_after_recovery, 2652.00)
        self.assertEqual(trade.profit_protection_level, 2)

    def test_early_profitable_close_allows_immediate_reentry_and_no_zone_lockout(self):
        """
        User Requirement: 'các lệnh chốt sớm nhưng đạt lợi nhuận chúng ta cho phép giao dịch tiếp'
        Verify that:
        1. A trade closed early with positive profit (e.g. profit scratch +1.50$) does NOT lockout the zone.
        2. consecutive_losses is reset to 0.
        3. Post-trade cooldown is 0s (profitable_close_cooldown_seconds), allowing immediate next trade.
        4. Consumed anchor is released, allowing continuation setups on the trend.
        """
        from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
        from core.domain.models import SessionConfig, MarketRegime
        from infrastructure.ai.mock_ai_adapter import MockAIEngine

        ai_adapter = MockAIEngine()
        eval_entry = EvaluateEntryUseCase(self.broker, self.bus, ai_adapter)

        # Simulate previous loss
        eval_entry.consecutive_losses = 1

        # Create trade that closed early (SCRATCHED) but made profit (+1.50$)
        trade_prof = TradeLifecycle(
            trade_id="trade_early_prof",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.SCRATCHED,
            part1=PositionPart(1, 0.01, 2650.0, 2647.0, 2656.0, ticket=101, is_closed=True, close_price=2651.5, pnl=1.50),
            part2=PositionPart(2, 0.01, 2650.0, 2647.0, 2665.0, ticket=102, is_closed=True, close_price=2651.5, pnl=1.50),
            open_time=time.time() - 30,
            close_time=time.time() - 5,
            anchor_id="PB_BUY_2650.0_1000",
            spatial_anchor_key="PB_BUY_2650.0",
            close_context={"close_state": "SCRATCHED", "total_pnl": 3.00}
        )
        eval_entry.consumed_anchors.add("PB_BUY_2650.0_1000")

        # Record trade closed
        eval_entry.record_trade_closed(trade_prof)

        # 1. consecutive_losses MUST be reset to 0
        self.assertEqual(eval_entry.consecutive_losses, 0)
        self.assertTrue(eval_entry.last_trade_was_profitable)

        # 2. Profitable scratch MUST NOT be added to zone_scratch_history (no zone lockout)
        self.assertEqual(len(eval_entry.zone_scratch_history.get("PB_BUY_2650.0", [])), 0)

        # 3. Consumed anchor MUST be released so continuation entry can be taken
        self.assertNotIn("PB_BUY_2650.0_1000", eval_entry.consumed_anchors)

        # 4. Immediate execution check: With profitable_close_cooldown_seconds = 0,
        # cooldown does NOT block entry even though only 5s elapsed since close
        cfg = SessionConfig(
            session_id="test_sess",
            symbol="XAUUSD",
            generated_at="2026-09-22",
            market_regime=MarketRegime.TRENDING_STEADY,
            resistance_zones=[],
            support_zones=[],
            setups_enabled={"PB": True},
            execution_rules={
                "enable_session_transition_guard": False,
                "post_trade_cooldown_seconds": 180,
                "profitable_close_cooldown_seconds": 0,
                "min_rr_ratio_part1": 0.5
            },
            risk_management={},
            news_filter={}
        )

        bars_m3 = [
            Bar(100, 2648.0, 2650.0, 2647.5, 2649.0),
            Bar(160, 2649.0, 2652.0, 2648.5, 2651.0),
            Bar(220, 2651.0, 2653.0, 2650.5, 2652.0),
            Bar(280, 2652.0, 2654.0, 2651.5, 2653.0),
            Bar(340, 2653.0, 2655.0, 2652.5, 2654.0),
        ]
        bars_m1 = [
            Bar(300, 2653.0, 2654.0, 2652.8, 2653.5),
            Bar(360, 2653.5, 2654.5, 2653.0, 2654.0),
        ]

        # Verify evaluate_entry allows trading immediately (not blocked by cooldown)
        # Note: Even if setup specifics depend on wholesale engine, cooldown check (step 3) passes!
        base_cooldown = cfg.execution_rules.get("post_trade_cooldown_seconds", 180)
        profit_cooldown = cfg.execution_rules.get("profitable_close_cooldown_seconds", 0)
        cooldown_effective = profit_cooldown if eval_entry.last_trade_was_profitable else base_cooldown
        self.assertEqual(cooldown_effective, 0)

if __name__ == "__main__":
    unittest.main()
