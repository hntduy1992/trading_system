"""
Unit tests for Minimum $1 Profit Guarantee and Early Breakeven Lock
"""
import unittest
import asyncio
import time
from core.domain.models import (
    Bar, OrderSide, PositionPart, PositionState, SetupType,
    TradeLifecycle, get_instrument_profile, calculate_pnl
)
from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.bus.async_event_bus import AsyncEventBus

class TestMinProfitAndEarlyLock(unittest.TestCase):
    def setUp(self):
        self.broker = PaperBroker(initial_balance=10000.0, symbol="XAUUSD")
        self.bus = AsyncEventBus()
        self.lifecycle = ManageLifecycleUseCase(self.broker, self.bus)
        from config import CONFIG
        self._orig_lock = getattr(CONFIG.risk, "ENABLE_EARLY_PROFIT_LOCK", False)
        self._orig_trail = getattr(CONFIG.risk, "ENABLE_SL_TRAILING", False)
        self._orig_pp_trail = getattr(CONFIG.profit_protection, "ENABLE_SL_TRAILING", False)
        self._orig_be_t1 = getattr(CONFIG.profit_protection, "MOVE_SL_TO_BE_ON_T1", False)
        CONFIG.risk.ENABLE_EARLY_PROFIT_LOCK = True
        CONFIG.risk.ENABLE_SL_TRAILING = True
        CONFIG.profit_protection.ENABLE_SL_TRAILING = True
        CONFIG.profit_protection.MOVE_SL_TO_BE_ON_T1 = True

    def tearDown(self):
        from config import CONFIG
        CONFIG.risk.ENABLE_EARLY_PROFIT_LOCK = self._orig_lock
        CONFIG.risk.ENABLE_SL_TRAILING = self._orig_trail
        CONFIG.profit_protection.ENABLE_SL_TRAILING = self._orig_pp_trail
        CONFIG.profit_protection.MOVE_SL_TO_BE_ON_T1 = self._orig_be_t1

    def test_calculate_pnl_xauusd(self):
        profile = get_instrument_profile("XAUUSD")
        # BUY 0.01 lot: Entry 2650.00, Exit 2651.00 (+1.0 point) -> Exactly $1.00 USD
        pnl = calculate_pnl(2650.00, 2651.00, 0.01, OrderSide.BUY, profile)
        self.assertEqual(pnl, 1.00)

        # SELL 0.01 lot: Entry 2650.00, Exit 2648.50 (+1.5 points) -> Exactly $1.50 USD
        pnl_sell = calculate_pnl(2650.00, 2648.50, 0.01, OrderSide.SELL, profile)
        self.assertEqual(pnl_sell, 1.50)

    def test_early_breakeven_lock_in_position(self):
        """
        When price moves into >= $2.00 profit while IN_POSITION and early lock is enabled,
        the system must automatically adjust SL to entry + buffer (locking >= $2.00 profit).
        """
        from config import CONFIG
        CONFIG.risk.ENABLE_EARLY_PROFIT_LOCK = True
        CONFIG.risk.ENABLE_SL_TRAILING = True

        part1 = PositionPart(1, 0.01, 2650.00, 2647.00, 2656.00, ticket=101)
        part2 = PositionPart(2, 0.01, 2650.00, 2647.00, 2665.00, ticket=102)
        trade = TradeLifecycle(
            trade_id="test_early_lock",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.IN_POSITION,
            part1=part1,
            part2=part2,
            open_time=time.time()
        )

        # Bar: Price reaches High 2652.50, Close 2652.20 (>= 2.0 point profit)
        bars_m1 = [
            Bar(timestamp=100, open=2650.00, high=2652.50, low=2649.80, close=2652.20)
        ]

        asyncio.run(self.lifecycle.update(trade, bars_m1, []))

        # SL should now be raised to entry + be_buffer (2650.00 + 2.00 = 2652.00)
        self.assertTrue(trade.early_profit_locked)
        self.assertGreaterEqual(trade.part1.sl_price, 2652.00)
        self.assertGreaterEqual(trade.part2.sl_price, 2652.00)

        # Reset config
        CONFIG.risk.ENABLE_EARLY_PROFIT_LOCK = False
        CONFIG.risk.ENABLE_SL_TRAILING = False

    def test_t1_hit_guarantees_min_profit_and_locks_part2(self):
        """
        When T1 hits, Part 1 must close with >= $2.00 profit,
        and Part 2 SL must be locked at least be_buffer (>= $2.00 profit).
        """
        from config import CONFIG
        CONFIG.profit_protection.MOVE_SL_TO_BE_ON_T1 = True

        part1 = PositionPart(1, 0.01, 2650.00, 2647.00, 2652.50, ticket=201)
        part2 = PositionPart(2, 0.01, 2650.00, 2647.00, 2665.00, ticket=202)
        trade = TradeLifecycle(
            trade_id="test_t1_lock",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.IN_POSITION,
            part1=part1,
            part2=part2,
            open_time=time.time()
        )

        # Bar hits TP1 (High >= 2652.50)
        bars_m1 = [
            Bar(timestamp=200, open=2651.00, high=2653.00, low=2650.50, close=2652.80)
        ]

        asyncio.run(self.lifecycle.update(trade, bars_m1, []))

        # Part 1 must be closed with profit >= $2.00 ($2.50 on 0.01 lot)
        self.assertTrue(trade.part1.is_closed)
        self.assertGreaterEqual(trade.part1.pnl, 2.00)
        self.assertEqual(trade.state, PositionState.TRAILING_STOP)

        # Part 2 SL must be set to at least 2650.00 + 2.00 = 2652.00
        self.assertGreaterEqual(trade.part2.sl_price, 2652.00)

        CONFIG.profit_protection.MOVE_SL_TO_BE_ON_T1 = False

    def test_profit_scratch_closes_with_profit(self):
        """
        If a trade is scratched after timeout while in positive profit >= $1.00,
        it should secure the profit and log total_pnl >= $1.00.
        """
        part1 = PositionPart(1, 0.01, 2650.00, 2647.00, 2665.00, ticket=301)
        part2 = PositionPart(2, 0.01, 2650.00, 2647.00, 2675.00, ticket=302)
        trade = TradeLifecycle(
            trade_id="test_profit_scratch",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.IN_POSITION,
            part1=part1,
            part2=part2,
            open_time=time.time()
        )
        trade.last_bar_timestamp = 200
        trade.m1_bars_in_trade = 15  # Exceeded scratch timeout (including dynamic profit extension)

        # Bar closed at 2651.50 (+1.50 points profit = +$3.00 for 0.02 total lot)
        bars_m1 = [
            Bar(timestamp=300, open=2651.00, high=2651.80, low=2650.80, close=2651.50)
        ]

        asyncio.run(self.lifecycle.update(trade, bars_m1, []))

        self.assertEqual(trade.state, PositionState.SCRATCHED)
        self.assertGreaterEqual(trade.total_pnl, 1.00)
        self.assertIn("Profit Secured", trade.close_context.get("close_reason", ""))

if __name__ == "__main__":
    unittest.main()
