"""
Unit tests for Candlestick Exit Engine & Fixed SL (Anti-Premature Exit)
Verifies:
1. Rejection Pin Bars (Shooting Star / Hammer) trigger active market exit.
2. Engulfing Bars trigger active market exit.
3. Momentum Reversal & Climax Exhaustion bars trigger active market exit.
4. Stop Loss remains strictly FIXED at initial technical level (no trailing/moving).
5. Integration with ManageLifecycleUseCase state machine.
"""
import unittest
import asyncio
import time
from core.domain.models import (
    Bar, OrderSide, PositionPart, PositionState, SetupType,
    TradeLifecycle, get_instrument_profile
)
from core.domain.rules.candlestick_engine import CandlestickEngine
from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.bus.async_event_bus import AsyncEventBus
from config import CONFIG

class TestCandlestickExits(unittest.TestCase):

    def setUp(self):
        self.broker = PaperBroker(initial_balance=10000.0, symbol="XAUUSD")
        self.bus = AsyncEventBus()
        self.lifecycle = ManageLifecycleUseCase(self.broker, self.bus)

    def _create_buy_trade(self, state=PositionState.TRAILING_STOP, entry=2650.00, sl=2646.00):
        part1 = PositionPart(1, 0.01, entry, sl, 2654.00, ticket=101, is_closed=(state == PositionState.TRAILING_STOP), close_price=2654.00)
        part2 = PositionPart(2, 0.01, entry, sl, 2665.00, ticket=102)
        trade = TradeLifecycle(
            trade_id="test_candle_exit_buy",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=state,
            part1=part1,
            part2=part2,
            open_time=time.time()
        )
        trade.initial_risk_dist = abs(entry - sl)
        return trade

    def _create_sell_trade(self, state=PositionState.TRAILING_STOP, entry=2650.00, sl=2654.00):
        part1 = PositionPart(1, 0.01, entry, sl, 2646.00, ticket=201, is_closed=(state == PositionState.TRAILING_STOP), close_price=2646.00)
        part2 = PositionPart(2, 0.01, entry, sl, 2635.00, ticket=202)
        trade = TradeLifecycle(
            trade_id="test_candle_exit_sell",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.SELL,
            state=state,
            part1=part1,
            part2=part2,
            open_time=time.time()
        )
        trade.initial_risk_dist = abs(entry - sl)
        return trade

    def test_shooting_star_pinbar_exit_buy(self):
        """
        For a BUY trade, an upper-wick Pin Bar (Shooting Star)
        with upper wick >= 55% of range closing near the low MUST trigger market exit.
        """
        trade = self._create_buy_trade(state=PositionState.TRAILING_STOP)

        # Bar 1: Normal bullish bar
        bar1 = Bar(timestamp=100, open=2653.0, high=2655.0, low=2652.8, close=2654.8)
        # Bar 2: Shooting star at high 2658.0: Open 2654.8, High 2658.0, Low 2654.5, Close 2654.9
        # Range = 3.5, Upper wick = 2658.0 - 2654.9 = 3.1 (88.5% of range), Lower wick = 0.3
        bar2 = Bar(timestamp=160, open=2654.8, high=2658.0, low=2654.5, close=2654.9)

        bars_m1 = [bar1, bar2]
        should_exit, reason = CandlestickEngine.evaluate_candlestick_exit(trade, bars_m1, curr_price=2654.9)
        self.assertTrue(should_exit)
        self.assertIn("PINBAR_REJECTION", reason)

        # Test integration with ManageLifecycleUseCase
        asyncio.run(self.lifecycle.update(trade, bars_m1, []))
        self.assertEqual(trade.state, PositionState.FULLY_CLOSED)
        self.assertTrue(trade.part2.is_closed)
        self.assertIn("PINBAR_REJECTION", trade.close_context.get("close_reason", ""))

    def test_bearish_engulfing_exit_buy(self):
        """
        For a BUY trade, a Bearish Engulfing Bar that opens near previous close
        and closes completely below previous bar's low MUST trigger market exit.
        """
        trade = self._create_buy_trade(state=PositionState.TRAILING_STOP)

        # Bar 1: Bullish bar (Low 2653.0, High 2655.0, Close 2654.8)
        bar1 = Bar(timestamp=100, open=2653.2, high=2655.0, low=2653.0, close=2654.8)
        # Bar 2: Bearish engulfing bar (Open 2654.8, High 2655.2, Low 2652.2, Close 2652.5)
        # Closes at 2652.5 < Bar 1 Low 2653.0
        bar2 = Bar(timestamp=160, open=2654.8, high=2655.2, low=2652.2, close=2652.5)

        bars_m1 = [bar1, bar2]
        should_exit, reason = CandlestickEngine.evaluate_candlestick_exit(trade, bars_m1, curr_price=2652.5)
        self.assertTrue(should_exit)
        self.assertIn("BEARISH_ENGULFING", reason)

        # Test integration with ManageLifecycleUseCase
        asyncio.run(self.lifecycle.update(trade, bars_m1, []))
        self.assertEqual(trade.state, PositionState.FULLY_CLOSED)
        self.assertIn("BEARISH_ENGULFING", trade.close_context.get("close_reason", ""))

    def test_hammer_pinbar_exit_sell(self):
        """
        For a SELL trade, a lower-wick Pin Bar (Hammer)
        with lower wick >= 55% of range closing near the high MUST trigger market exit.
        """
        trade = self._create_sell_trade(state=PositionState.TRAILING_STOP)

        # Bar 1: Normal bearish bar
        bar1 = Bar(timestamp=100, open=2647.0, high=2647.2, low=2645.0, close=2645.2)
        # Bar 2: Hammer pin bar at low 2641.0: Open 2645.2, High 2645.5, Low 2641.0, Close 2645.1
        # Range = 4.5, Lower wick = 2645.1 - 2641.0 = 4.1 (91% of range), Upper wick = 0.3
        bar2 = Bar(timestamp=160, open=2645.2, high=2645.5, low=2641.0, close=2645.1)

        bars_m1 = [bar1, bar2]
        should_exit, reason = CandlestickEngine.evaluate_candlestick_exit(trade, bars_m1, curr_price=2645.1)
        self.assertTrue(should_exit)
        self.assertIn("PINBAR_REJECTION", reason)

        # Test integration with ManageLifecycleUseCase
        asyncio.run(self.lifecycle.update(trade, bars_m1, []))
        self.assertEqual(trade.state, PositionState.FULLY_CLOSED)
        self.assertTrue(trade.part2.is_closed)
        self.assertIn("PINBAR_REJECTION", trade.close_context.get("close_reason", ""))

    def test_bullish_engulfing_exit_sell(self):
        """
        For a SELL trade, a Bullish Engulfing Bar that opens near previous close
        and closes completely above previous bar's high MUST trigger market exit.
        """
        trade = self._create_sell_trade(state=PositionState.TRAILING_STOP)

        # Bar 1: Bearish bar (Low 2645.0, High 2647.0, Close 2645.2)
        bar1 = Bar(timestamp=100, open=2646.8, high=2647.0, low=2645.0, close=2645.2)
        # Bar 2: Bullish engulfing bar (Open 2645.2, High 2647.8, Low 2644.8, Close 2647.5)
        # Closes at 2647.5 > Bar 1 High 2647.0
        bar2 = Bar(timestamp=160, open=2645.2, high=2647.8, low=2644.8, close=2647.5)

        bars_m1 = [bar1, bar2]
        should_exit, reason = CandlestickEngine.evaluate_candlestick_exit(trade, bars_m1, curr_price=2647.5)
        self.assertTrue(should_exit)
        self.assertIn("BULLISH_ENGULFING", reason)

        # Test integration with ManageLifecycleUseCase
        asyncio.run(self.lifecycle.update(trade, bars_m1, []))
        self.assertEqual(trade.state, PositionState.FULLY_CLOSED)
        self.assertIn("BULLISH_ENGULFING", trade.close_context.get("close_reason", ""))

    def test_fixed_stop_loss_not_moved_when_sl_trailing_disabled(self):
        """
        User Requirement: 'bỏ dời sl, lệnh đa bị chốt rất nhanh, tha vào đó chốt lệnh theo dấu hiệu nến'
        Verify that with ENABLE_SL_TRAILING = False:
        - In IN_POSITION: SL remains strictly at original initial SL (2646.00).
        - After T1 Hit: Part 2 SL remains strictly at original SL (2646.00) (not dragged into noise).
        - Price swings do NOT drag or creep SL closer.
        """
        # Ensure default disabled settings are active
        self.assertFalse(CONFIG.profit_protection.ENABLE_SL_TRAILING)
        self.assertFalse(CONFIG.risk.ENABLE_SL_TRAILING)

        part1 = PositionPart(1, 0.01, 2650.00, 2646.00, 2654.00, ticket=301)
        part2 = PositionPart(2, 0.01, 2650.00, 2646.00, 2665.00, ticket=302)
        trade = TradeLifecycle(
            trade_id="test_fixed_sl",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.IN_POSITION,
            part1=part1,
            part2=part2,
            open_time=time.time()
        )

        # 1. Price moves to +1.20 points (2651.20) in IN_POSITION
        bar_early = Bar(timestamp=100, open=2650.0, high=2651.5, low=2649.8, close=2651.2)
        asyncio.run(self.lifecycle.update(trade, [bar_early], []))

        # SL must NOT move! Remains at 2646.00
        self.assertEqual(trade.part1.sl_price, 2646.00)
        self.assertEqual(trade.part2.sl_price, 2646.00)

        # 2. Price hits TP1 (2654.00) -> T1 Hit
        bar_t1 = Bar(timestamp=160, open=2651.2, high=2654.5, low=2651.0, close=2654.2)
        asyncio.run(self.lifecycle.update(trade, [bar_early, bar_t1], []))

        self.assertTrue(trade.part1.is_closed)
        self.assertEqual(trade.state, PositionState.TRAILING_STOP)
        # Part 2 SL must remain at initial SL 2646.00, giving maximum room without wick sweep!
        self.assertEqual(trade.part2.sl_price, 2646.00)

        # 3. Pullback bar dips to 2650.20 (below entry!):
        # A moving SL or BE SL would have been KILLED here, but our fixed SL survives!
        bar_pullback = Bar(timestamp=220, open=2654.0, high=2654.0, low=2650.2, close=2651.0)
        asyncio.run(self.lifecycle.update(trade, [bar_early, bar_t1, bar_pullback], []))

        # Trade must still be alive and active!
        self.assertEqual(trade.state, PositionState.TRAILING_STOP)
        self.assertFalse(trade.part2.is_closed)
        self.assertEqual(trade.part2.sl_price, 2646.00)

    def test_momentum_reversal_exit(self):
        """
        Verify that a large opposite trend bar (expansion >= 1.25 ATR) triggers market exit.
        """
        trade = self._create_buy_trade(state=PositionState.TRAILING_STOP)

        # Build 14 normal bars to establish ATR ~ 0.8, with the 14th bar being slightly bearish
        bars = [Bar(100 + i*60, 2650.0 + i*0.2, 2650.8 + i*0.2, 2649.8 + i*0.2, 2650.5 + i*0.2) for i in range(13)]
        bars.append(Bar(100 + 13*60, open=2653.0, high=2653.2, low=2652.5, close=2652.8)) # Bearish bar
        # Add sudden strong bearish momentum bar breaking previous low with range 1.8 (> 1.25 * 0.8)
        opp_bar = Bar(timestamp=100 + 14*60, open=2653.8, high=2654.0, low=2652.0, close=2652.1)
        bars.append(opp_bar)

        should_exit, reason = CandlestickEngine.evaluate_candlestick_exit(trade, bars, curr_price=2652.1)
        self.assertTrue(should_exit)
        self.assertIn("MOMENTUM_REVERSAL", reason)


if __name__ == "__main__":
    unittest.main()
