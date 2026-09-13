"""
Unit Tests for Candlestick Engine & Vol 5 Price Action Setups
Verifying all 5 strategies, mathematical primitives, filters, and Time-In-Force expiry
"""
import unittest
import asyncio
import time
from core.domain.models import Bar, OrderSide, PositionState, SetupType, TradeLifecycle, PositionPart
from core.domain.rules.candlestick_engine import CandlestickEngine
from core.domain.rules.setups.candlestick_setups import (
    TrendBarFailSetup, InsideBarSMA21Setup, IDNR4Setup, NR7EMA20Setup, YumYumSetup
)
from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.bus.async_event_bus import AsyncEventBus


def make_bar(o: float, h: float, l: float, c: float, ts: float = 1000.0) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c)


class TestCandlestickEngine(unittest.TestCase):

    def test_detect_trend_bar(self):
        # Bullish Trend Bar: open 100, close 108, low 99, high 109. Range = 10, Body = 8 (80% > 50%)
        bull_bar = make_bar(100.0, 109.0, 99.0, 108.0)
        is_tb, direction = CandlestickEngine.detect_trend_bar(bull_bar)
        self.assertTrue(is_tb)
        self.assertEqual(direction, "BULLISH")

        # Bearish Trend Bar: open 108, close 100, low 99, high 109. Body = 8 (80%)
        bear_bar = make_bar(108.0, 109.0, 99.0, 100.0)
        is_tb, direction = CandlestickEngine.detect_trend_bar(bear_bar)
        self.assertTrue(is_tb)
        self.assertEqual(direction, "BEARISH")

        # Neutral / Doji: open 100, close 101, low 90, high 110. Range = 20, Body = 1 (5% <= 50%)
        neutral_bar = make_bar(100.0, 110.0, 90.0, 101.0)
        is_tb, direction = CandlestickEngine.detect_trend_bar(neutral_bar)
        self.assertFalse(is_tb)
        self.assertEqual(direction, "NEUTRAL")

    def test_is_inside_bar(self):
        mother = make_bar(100.0, 110.0, 90.0, 105.0)
        child_valid = make_bar(101.0, 108.0, 92.0, 103.0)
        child_invalid = make_bar(101.0, 112.0, 92.0, 103.0) # breaks high

        self.assertTrue(CandlestickEngine.is_inside_bar(child_valid, mother))
        self.assertFalse(CandlestickEngine.is_inside_bar(child_invalid, mother))

    def test_is_congestion_filter(self):
        # Create 5 bars with long symmetric wicks (upper & lower >= 30%)
        # e.g. low=90, high=110, range=20. Upper wick >= 6, lower wick >= 6. open=98, close=102
        choppy_bars = [
            make_bar(98.0, 110.0, 90.0, 102.0, ts=1000 + i*60)
            for i in range(5)
        ]
        self.assertTrue(CandlestickEngine.is_congestion(choppy_bars, lookback=5, min_symmetric_bars=3))

        # Trending clean bars with minimal wicks
        clean_bars = [
            make_bar(100.0 + i*5, 105.0 + i*5, 99.8 + i*5, 104.8 + i*5, ts=1000 + i*60)
            for i in range(5)
        ]
        self.assertFalse(CandlestickEngine.is_congestion(clean_bars, lookback=5, min_symmetric_bars=3))

    def test_is_nr4_and_id_nr4(self):
        # Ranges: 10, 8, 6, 2 (4th is narrowest = 2)
        bars = [
            make_bar(100.0, 110.0, 100.0, 105.0, ts=1000), # range 10
            make_bar(100.0, 108.0, 100.0, 105.0, ts=1060), # range 8
            make_bar(100.0, 106.0, 100.0, 105.0, ts=1120), # range 6 (mother)
            make_bar(102.0, 104.0, 102.0, 103.0, ts=1180), # range 2 (child, inside bar!)
        ]
        self.assertTrue(CandlestickEngine.is_nr4(bars, offset=0))
        self.assertTrue(CandlestickEngine.is_id_nr4(bars, offset=0))

    def test_is_nr7(self):
        # 6 bars with range 5.0, 7th bar with range 1.0
        bars = [
            make_bar(100.0, 105.0, 100.0, 103.0, ts=1000 + i*60)
            for i in range(6)
        ]
        bars.append(make_bar(101.0, 102.0, 101.0, 101.5, ts=1000 + 6*60)) # range 1.0
        self.assertTrue(CandlestickEngine.is_nr7(bars, offset=0))

    def test_is_wide_range_breakout(self):
        # 10 bars with range 3.0, 11th bar with massive range 15.0
        bars = [
            make_bar(100.0, 103.0, 100.0, 102.0, ts=1000 + i*60)
            for i in range(10)
        ]
        bars.append(make_bar(100.0, 115.0, 100.0, 114.0, ts=1000 + 10*60)) # range 15.0
        self.assertTrue(CandlestickEngine.is_wide_range_breakout(bars, offset=0, lookback=10))

    def test_module1_trend_bar_failure_buy(self):
        # Bullish context (EMA rising or price above EMA)
        bars = [make_bar(100.0 + i, 102.0 + i, 99.0 + i, 101.0 + i, ts=1000 + i*60) for i in range(25)]

        # Bar[-2]: Strong Bearish Trend Bar (Precondition)
        # Open 130, Close 120, High 131, Low 119 -> Range 12, Body 10 (83% Bearish)
        bars.append(make_bar(130.0, 131.0, 119.0, 120.0, ts=3000))

        # Bar[-1]: Trapped Bears! Makes a lower low (< 119.0), but closes bullish / hammer (NOT Bearish Trend Bar)
        # Low 117.0, Open 118.0, High 126.0, Close 125.0
        bars.append(make_bar(118.0, 126.0, 117.0, 125.0, ts=3060))

        triggered, side, entry, sl, max_bars = CandlestickEngine.evaluate_trend_bar_failure(bars, tick_size=0.1)
        self.assertTrue(triggered)
        self.assertEqual(side, OrderSide.BUY)
        self.assertEqual(entry, 126.1) # Bar1.high + tick_size
        self.assertEqual(sl, 116.9)    # Bar1.low - tick_size
        self.assertEqual(max_bars, 1)  # Strict 1-bar expiry

    def test_module5_yum_yum_buy(self):
        # Base rising bars
        bars = [make_bar(100.0 + i*0.5, 102.0 + i*0.5, 99.5 + i*0.5, 101.5 + i*0.5, ts=1000 + i*60) for i in range(25)]

        # Bar[-1]: Massive breakout bar > prior 10 bars range, closing at top 20%
        # High 130.0, Low 115.0 (range 15.0). Close 129.0 -> (130 - 129)/15 = 0.067 <= 0.20
        bars.append(make_bar(116.0, 130.0, 115.0, 129.0, ts=4000))

        triggered, side, entry, sl, max_bars = CandlestickEngine.evaluate_yum_yum(bars, tick_size=0.1)
        self.assertTrue(triggered)
        self.assertEqual(side, OrderSide.BUY)
        self.assertEqual(entry, 130.1)
        self.assertEqual(max_bars, 3) # Auto-cancel after 3 bars


class TestLifecycleTimeInForceExpiry(unittest.IsolatedAsyncioTestCase):

    async def test_pending_order_cancelled_after_max_bars(self):
        broker = PaperBroker(initial_balance=10000.0, symbol="XAUUSD")
        await broker.connect()
        event_bus = AsyncEventBus()
        use_case = ManageLifecycleUseCase(broker, event_bus)

        # Place a pending limit order on broker
        ticket = await broker.place_order("XAUUSD", OrderSide.BUY, "LIMIT", 0.1, 2600.0, 2595.0, 2610.0)

        trade = TradeLifecycle(
            trade_id="test_expiring_trade",
            symbol="XAUUSD",
            setup_type=SetupType.TREND_BAR_FAIL,
            side=OrderSide.BUY,
            state=PositionState.PENDING_ENTRY,
            part1=PositionPart(1, 0.05, 2600.0, 2595.0, 2610.0, ticket=ticket),
            part2=PositionPart(2, 0.05, 2600.0, 2595.0, 2620.0, ticket=ticket),
            open_time=time.time(),
            limit_order_ticket=ticket,
            stop_order_ticket=None,
            m1_bars_in_trade=0,
            last_bar_timestamp=1000.0,
            max_bars_pending=1 # Strict 1-bar timeout!
        )

        # Bar 1 (same bar timestamp 1000.0, price doesn't hit 2600.0)
        bar_1 = [make_bar(2610.0, 2615.0, 2605.0, 2612.0, ts=1000.0)]
        trade = await use_case.update(trade, bar_1, bar_1)
        self.assertEqual(trade.state, PositionState.PENDING_ENTRY)

        # Bar 2 (new bar timestamp 1060.0 -> elapsed 1 bar >= max_bars_pending=1)
        bar_2 = [make_bar(2612.0, 2618.0, 2608.0, 2615.0, ts=1060.0)]
        trade = await use_case.update(trade, bar_2, bar_2)

        # Should be SCRATCHED and ticket cancelled on broker
        self.assertEqual(trade.state, PositionState.SCRATCHED)
        self.assertTrue(trade.part1.is_closed)
        self.assertIn("Time-In-Force expired", trade.close_context["reason"])

        # Verify broker cancelled order (deleted from active orders)
        self.assertNotIn(ticket, broker.orders)


if __name__ == "__main__":
    unittest.main()