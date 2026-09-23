"""
Unit tests for Discipline in Trade Holding & Minimum $2.00 Profit per 0.01 Lot Guarantee
"""
import unittest
import asyncio
import time
from core.domain.models import (
    Bar, OrderSide, PositionPart, PositionState, SetupType,
    TradeLifecycle, get_instrument_profile, calculate_pnl
)
from core.domain.rules.wholesale_engine import WholesaleEngine
from core.domain.rules.candlestick_engine import CandlestickEngine
from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.bus.async_event_bus import AsyncEventBus
from config import CONFIG

class TestDisciplineMinProfit(unittest.TestCase):
    def setUp(self):
        self.broker = PaperBroker(initial_balance=10000.0, symbol="XAUUSD")
        self.bus = AsyncEventBus()
        self.lifecycle = ManageLifecycleUseCase(self.broker, self.bus)

    def test_in_position_candlestick_exit_blocked_when_profit_below_2_usd(self):
        """
        In IN_POSITION, even if a reversal candlestick pattern appears,
        the trade MUST NOT be closed prematurely if profit < $2.00 per 0.01 lot.
        """
        # 0.01 lot BUY entry at 2650.00, SL 2646.00, T1 2655.00
        part1 = PositionPart(1, 0.01, 2650.00, 2646.00, 2655.00, ticket=101)
        part2 = PositionPart(2, 0.0, 2650.00, 2646.00, 2660.00, ticket=0)
        trade = TradeLifecycle(
            trade_id="test_discipline_hold",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.IN_POSITION,
            part1=part1,
            part2=part2,
            open_time=time.time()
        )
        trade.m1_bars_in_trade = 6
        trade.last_bar_timestamp = 100

        # Bar 1: Price only reached 2651.00 (profit = +$1.00, which is < $2.00 required)
        # and forms a shooting star bearish pin bar
        bar1 = Bar(timestamp=160, open=2650.80, high=2651.50, low=2650.70, close=2650.90)
        bars_m1 = [
            Bar(timestamp=100, open=2650.00, high=2651.20, low=2649.80, close=2650.80),
            bar1
        ]

        asyncio.run(self.lifecycle.update(trade, bars_m1, []))

        # Must maintain position with discipline, NOT closed prematurely!
        self.assertEqual(trade.state, PositionState.IN_POSITION)
        self.assertFalse(trade.part1.is_closed)

    def test_in_position_candlestick_exit_allowed_when_profit_exceeds_2_usd(self):
        """
        In IN_POSITION, if profit >= $2.00 per 0.01 lot and held >= 5 bars,
        a genuine reversal candle CAN trigger disciplined exit.
        """
        part1 = PositionPart(1, 0.01, 2650.00, 2646.00, 2655.00, ticket=101)
        part2 = PositionPart(2, 0.0, 2650.00, 2646.00, 2660.00, ticket=0)
        trade = TradeLifecycle(
            trade_id="test_discipline_exit",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.IN_POSITION,
            part1=part1,
            part2=part2,
            open_time=time.time()
        )
        trade.m1_bars_in_trade = 6
        trade.last_bar_timestamp = 100

        # Bar 1: Bullish bar
        bar1 = Bar(timestamp=100, open=2650.00, high=2653.50, low=2650.00, close=2653.20)
        # Bar 2: Shooting star at high 2654.50 (< TP 2655.00), closing at 2652.80 (profit = +$2.80 >= $2.00)
        bar2 = Bar(timestamp=160, open=2653.20, high=2654.50, low=2652.60, close=2652.80)
        bars_m1 = [bar1, bar2]

        asyncio.run(self.lifecycle.update(trade, bars_m1, []))

        self.assertEqual(trade.state, PositionState.FULLY_CLOSED)
        self.assertTrue(trade.part1.is_closed)
        self.assertGreaterEqual(trade.total_pnl, 2.00)
        self.assertIn("PINBAR_REJECTION", trade.close_context.get("close_reason", ""))

    def test_trailing_stop_part2_candlestick_exit_requires_min_2_usd(self):
        """
        In TRAILING_STOP, Part 2 candlestick exit must NOT trigger if Part 2 profit < $2.00.
        """
        part1 = PositionPart(1, 0.01, 2650.00, 2646.00, 2654.00, ticket=201, is_closed=True, close_price=2654.00, pnl=4.00)
        part2 = PositionPart(2, 0.01, 2650.00, 2648.00, 2665.00, ticket=202)
        trade = TradeLifecycle(
            trade_id="test_part2_exit",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.TRAILING_STOP,
            part1=part1,
            part2=part2,
            open_time=time.time()
        )
        trade.initial_risk_dist = 4.0
        trade.last_trailing_bar_timestamp = 100

        # Bar: Price dropped to 2651.50 (Part 2 profit = +$1.50 < $2.00, low 2651.20 > SL 2648.00)
        bar1 = Bar(timestamp=100, open=2653.00, high=2654.00, low=2652.50, close=2653.50)
        bar2 = Bar(timestamp=160, open=2653.50, high=2654.00, low=2651.20, close=2651.50)
        bars_m1 = [bar1, bar2]

        asyncio.run(self.lifecycle.update(trade, bars_m1, []))

        # Part 2 should remain open because profit has not reached $2.00
        self.assertEqual(trade.state, PositionState.TRAILING_STOP)
        self.assertFalse(trade.part2.is_closed)

    def test_single_001_lot_trade_full_close_on_t1(self):
        """
        For a single 0.01 lot trade (lot_p2 == 0), hitting T1 must fully close
        the trade and record profit >= $2.00.
        """
        part1 = PositionPart(1, 0.01, 2650.00, 2646.00, 2653.00, ticket=301)
        part2 = PositionPart(2, 0.0, 2650.00, 2646.00, 2660.00, ticket=0)
        trade = TradeLifecycle(
            trade_id="test_single_001_t1",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.IN_POSITION,
            part1=part1,
            part2=part2,
            open_time=time.time()
        )

        # Bar reaches T1 (High >= 2653.00)
        bars_m1 = [
            Bar(timestamp=100, open=2651.00, high=2653.50, low=2650.50, close=2653.20)
        ]

        asyncio.run(self.lifecycle.update(trade, bars_m1, []))

        self.assertEqual(trade.state, PositionState.FULLY_CLOSED)
        self.assertTrue(trade.part1.is_closed)
        self.assertEqual(trade.part1.pnl, 3.00)
        self.assertEqual(trade.total_pnl, 3.00)
        self.assertIn("T1_TARGET_HIT_SINGLE_POSITION", trade.close_context.get("close_reason", ""))

    def test_wholesale_engine_guarantees_min_profit_distance_for_t1(self):
        """
        WholesaleEngine must ensure that T1 distance is at least min_profit_points
        (>= 2.00 points for Gold, ensuring >= $2.00 on 0.01 lot).
        """
        profile = get_instrument_profile("XAUUSD")
        self.assertEqual(profile.min_profit_points, 2.00)

        # Even with tight opposing swing (e.g. 2651.00, only 1.0 pt from entry 2650.00)
        # and tp_multiplier = 0.90, T1 must be clamped to at least entry + 2.00 = 2652.00
        ws = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            pullback_swing_price=2647.00,
            t1_price=2651.00,
            t2_price=2660.00,
            micro_stall_high=2650.50,
            micro_stall_low=2650.00,
            buffer_pts=profile.min_buffer_points,
            min_sl_distance=profile.min_sl_points,
            min_rr_ratio=1.0,
            sl_multiplier=1.20,
            tp_multiplier=0.90,
            min_profit_distance=profile.min_profit_points
        )

        reward_dist = ws.T1 - ws.recommended_entry
        self.assertGreaterEqual(reward_dist, 2.00)

if __name__ == "__main__":
    unittest.main()
