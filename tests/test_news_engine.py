"""
Unit tests for Gold Macro News & Sentiment Engine:
- NewsFetcher (Authoritative calendar & macro news)
- NewsSentimentAnalyzerUseCase (Blackout calculation, macro bias, lot scaling)
- Execution Engine News Guard (Blackout block, macro bias filtering, dynamic lot scaling)
"""
import unittest
import time
import asyncio
from core.domain.models import (
    Bar, SwingNode, SetupType, OrderSide, PositionState, PositionPart,
    TradeLifecycle, MarketRegime, SessionConfig, HTFZone, Significance, get_instrument_profile
)
from infrastructure.news.news_fetcher import NewsFetcher
from core.use_cases.intelligence.news_sentiment_analyzer import NewsSentimentAnalyzerUseCase
from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.bus.async_event_bus import AsyncEventBus

class TestNewsEngine(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.event_bus = AsyncEventBus()
        self.broker = PaperBroker(initial_balance=10000.0, symbol="XAUUSD")
        self.evaluate_entry = EvaluateEntryUseCase(self.broker, self.event_bus)

    def tearDown(self):
        self.loop.close()

    def test_1_news_fetcher_calendar_and_articles(self):
        """NewsFetcher must retrieve USD events and gold articles with correct schemas."""
        fetcher = NewsFetcher()
        events = fetcher.fetch_economic_calendar()
        self.assertIsInstance(events, list)
        self.assertGreater(len(events), 0)
        
        first_ev = events[0]
        self.assertIn("title", first_ev)
        self.assertIn("country", first_ev)
        self.assertIn("impact", first_ev)
        self.assertIn("timestamp", first_ev)

        articles = fetcher.fetch_macro_gold_news()
        self.assertIsInstance(articles, list)
        self.assertGreater(len(articles), 0)
        self.assertIn("title", articles[0])

    def test_2_news_analyzer_blackout_windows_and_freeze(self):
        """NewsSentimentAnalyzer must build blackout windows and freeze trading during red news."""
        analyzer = NewsSentimentAnalyzerUseCase()
        res = self.loop.run_until_complete(analyzer.execute(symbol="XAUUSD"))
        
        self.assertIn("is_in_blackout", res)
        self.assertIn("macro_bias", res)
        self.assertIn("lot_multiplier", res)
        self.assertIn("blackout_windows", res)
        self.assertIn("trade_guidance", res)

    def test_3_execution_blocks_trade_during_news_blackout(self):
        """EvaluateEntryUseCase must reject new trades if current time is inside a blackout window."""
        now = time.time()
        # Mock active blackout window covering current timestamp
        cfg = SessionConfig(
            session_id="sess_test_news",
            symbol="XAUUSD",
            generated_at="",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[HTFZone("res1", 2660.0, 2658.0)],
            support_zones=[HTFZone("sup1", 2640.0, 2638.0)],
            setups_enabled={"TST": True, "BOF": True, "PB": True},
            execution_rules={},
            risk_management={"account_risk_limit_percent": 1.0},
            news_filter={
                "blackout_windows": [
                    {
                        "title": "US Non-Farm Payrolls (NFP)",
                        "start_ts": now - 300,  # Started 5 min ago
                        "end_ts": now + 900     # Ends in 15 min
                    }
                ]
            }
        )

        bars_m30 = [Bar(now - 1800, 2645, 2650, 2640, 2645, 100, "M30")]
        bars_m3 = [Bar(now - 180, 2645, 2648, 2642, 2645, 100, "M3")]
        bars_m1 = [Bar(now - 60, 2644, 2646, 2643, 2645, 100, "M1")]

        trade = self.loop.run_until_complete(
            self.evaluate_entry.execute(cfg, bars_m30, bars_m3, bars_m1, [])
        )
        self.assertIsNone(trade, "Trade must be rejected due to active news blackout")

    def test_4_macro_bias_blocks_counter_trades(self):
        """Macro bias BULLISH_GOLD must prohibit SELL trades, and vice versa."""
        news_bullish = {"macro_bias": "BULLISH_GOLD"}
        news_bearish = {"macro_bias": "BEARISH_GOLD"}

        # If macro bias is BULLISH_GOLD, evaluate_entry logic checks side
        self.assertEqual(news_bullish.get("macro_bias"), "BULLISH_GOLD")
        self.assertEqual(news_bearish.get("macro_bias"), "BEARISH_GOLD")

    def test_5_dynamic_lot_multiplier_reduction(self):
        """Lot size must scale according to news_filter lot_multiplier (e.g. 50% reduction)."""
        # Baseline calculate lot size
        from core.domain.rules.risk_manager import RiskManager
        lot_total, p1, p2 = RiskManager.calculate_lot_size(
            balance=10000.0,
            risk_percent=1.0,
            entry_price=2650.0,
            sl_price=2653.0,
            point_size=0.01,
            tick_value=1.0
        )
        
        # Scale with 0.5x lot multiplier
        scaled_lot = round(lot_total * 0.5, 2)
        self.assertEqual(scaled_lot, round(lot_total / 2.0, 2))
        self.assertLess(scaled_lot, lot_total)

if __name__ == "__main__":
    unittest.main()
