import unittest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock
from core.domain.models import (
    SessionConfig, MarketRegime, HTFZone, Bar, OrderSide, SetupType, 
    PositionPart, PositionState, TradeLifecycle
)
from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
from core.use_cases.intelligence.news_sentiment_analyzer import NewsSentimentAnalyzerUseCase
from run import SystemOrchestrator

class TestReverseScannerAndNewsFilter(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_medium_news_blackout_rejection(self):
        """Verify that Medium impact USD events like Retail Sales trigger blackout and block entries."""
        now = time.time()
        analyzer = NewsSentimentAnalyzerUseCase()
        # Mock fetch_economic_calendar returning Retail Sales as MEDIUM at current time
        analyzer.news_fetcher.fetch_economic_calendar = MagicMock(return_value=[
            {
                "id": "ff_test_retail_sales",
                "title": "Core Retail Sales m/m",
                "country": "USD",
                "impact": "MEDIUM",
                "timestamp": now,
                "datetime_str": "2026-09-16 19:30:00"
            }
        ])
        res = self.loop.run_until_complete(analyzer.execute(symbol="XAUUSD", include_medium_impact=True, force_refresh=True))
        
        self.assertTrue(res["is_in_blackout"], "Medium-impact Retail Sales MUST trigger news blackout")
        self.assertIn("Core Retail Sales m/m", res["active_blackout_reason"])

        # Test evaluate_entry rejection with this blackout window
        broker = AsyncMock()
        event_bus = AsyncMock()
        evaluate_entry = EvaluateEntryUseCase(broker=broker, event_bus=event_bus)

        cfg = SessionConfig(
            session_id="test_sess",
            symbol="XAUUSD",
            generated_at="",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[HTFZone("r1", 2650, 2648)],
            support_zones=[HTFZone("s1", 2630, 2628)],
            setups_enabled={"TST": True, "YUM_YUM": True},
            execution_rules={},
            risk_management={},
            news_filter={"blackout_windows": res["blackout_windows"]}
        )

        bars_m30 = [Bar(now - 1800, 2640, 2642, 2638, 2640, 100, "M30")]
        bars_m3 = [Bar(now - 180, 2640, 2642, 2638, 2640, 100, "M3")]
        bars_m1 = [Bar(now - 60, 2640, 2642, 2638, 2640, 100, "M1")]

        trade = self.loop.run_until_complete(
            evaluate_entry.execute(cfg, bars_m30, bars_m3, bars_m1, [])
        )
        self.assertIsNone(trade, "EvaluateEntry must strictly reject entry during Retail Sales blackout")

    def test_reverse_scanner_force_closes_orphaned_position(self):
        """Verify that reverse scanner finds position open on MT5 that is marked closed in Server, and force-closes it."""
        system = SystemOrchestrator(mode="paper", symbol="XAUUSD")
        
        # Setup closed trade in server state
        closed_trade = TradeLifecycle(
            trade_id="ytc_1789561801084",
            symbol="XAUUSD",
            setup_type=SetupType.YUM_YUM,
            side=OrderSide.SELL,
            state=PositionState.STOPPED_OUT,
            part1=PositionPart(1, 0.01, 4340.0, 4344.0, 4330.0, ticket=3828164610, is_closed=True),
            part2=PositionPart(2, 0.01, 4340.0, 4344.0, 4320.0, ticket=3828164610, is_closed=True),
            open_time=time.time() - 30,
            close_time=time.time() - 20
        )
        system.state["active_trades"] = []
        system.state["closed_trades"] = [closed_trade]

        # Mock broker returning this ticket still OPEN on MT5!
        system.broker.get_open_positions = AsyncMock(return_value=[
            {
                "ticket": 3828164610,
                "symbol": "XAUUSD",
                "type": "SELL",
                "volume": 0.02,
                "price_open": 4340.0,
                "sl": 4344.0,
                "tp": 4330.0,
                "magic": 2102026,
                "comment": "ytc_1789561801084_YUM_YUM"
            }
        ])
        system.broker.close_position = AsyncMock(return_value=True)

        # Run reverse reconciliation
        self.loop.run_until_complete(system.reconcile_and_scan_broker_positions(latest_m1_close=4341.0))

        # Check: broker.close_position MUST have been called with ticket 3828164610
        system.broker.close_position.assert_awaited_with(3828164610, 0.02)

if __name__ == "__main__":
    unittest.main()
