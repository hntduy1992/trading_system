"""
Unit Tests for Market Touch Entry & Broker Lot Synchronization
Verifies:
1. When setup triggers but price is approaching entry, NO limit order is sent to MT5/broker (held as candidate on server).
2. When price touches entry, MARKET order is executed immediately to MT5/broker.
3. Multi-part lot dispatch: 0.01 lot single order vs 0.02 (0.01 + 0.01) separate part orders.
4. MT5 broker never falsely transitions PENDING_ENTRY to IN_POSITION via bar price fallback.
"""
import unittest
import asyncio
import time
from typing import Dict, Any, List, Optional
from core.domain.models import (
    Bar, OrderSide, PositionState, SetupType, SessionConfig, HTFZone,
    TradeLifecycle, PositionPart, MarketRegime
)
from core.domain.rules.risk_manager import RiskManager
from core.use_cases.execution.evaluate_entry import EvaluateEntryUseCase
from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase
from infrastructure.brokers.paper_broker import PaperBroker
from infrastructure.bus.async_event_bus import AsyncEventBus


class MockBrokerSpy:
    """Mock broker recording all place_order calls to inspect order_type and volume."""
    def __init__(self, is_connected_mt5: bool = False):
        self.orders: List[Dict[str, Any]] = []
        self.connected = is_connected_mt5
        self.ticket_counter = 500000

    async def get_account_balance(self) -> float:
        return 10000.0

    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        return {"bid": 2644.0, "ask": 2644.3}

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: str,
        volume: float,
        price: float,
        sl: float,
        tp: float,
        comment: str = ""
    ) -> Optional[int]:
        self.ticket_counter += 1
        record = {
            "ticket": self.ticket_counter,
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "volume": volume,
            "price": price,
            "sl": sl,
            "tp": tp,
            "comment": comment
        }
        self.orders.append(record)
        return self.ticket_counter

    async def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        # Returns empty list simulating order not yet filled
        return []

    async def cancel_order(self, ticket: int) -> bool:
        return True

    async def close_position(self, ticket: int, volume: Optional[float] = None) -> bool:
        return True


class TestMarketTouchAndLotSync(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.event_bus = AsyncEventBus()
        self.broker_spy = MockBrokerSpy(is_connected_mt5=True)
        self.evaluate_entry = EvaluateEntryUseCase(self.broker_spy, self.event_bus)

        self.session_config = SessionConfig(
            session_id="test_sync",
            symbol="XAUUSD",
            generated_at="2026-09-17",
            market_regime=MarketRegime.SIDEWAYS_RANGE,
            resistance_zones=[HTFZone("res1", 2670.0, 2665.0)],
            support_zones=[HTFZone("sup1", 2645.0, 2640.0)],
            setups_enabled={"TST": True},
            execution_rules={"enable_session_transition_guard": False, "enable_ai_pre_entry": False, "min_rr_ratio_part1": 1.0, "post_trade_cooldown_seconds": 0},
            risk_management={"account_risk_limit_percent": 1.0, "fixed_lot_size": 0.02},
            news_filter={},
            session_tag="TEST"
        )

    def _build_bars(self, current_close: float):
        bars_m3 = [
            Bar(300, 2655.0, 2660.0, 2650.0, 2652.0),
            Bar(600, 2652.0, 2655.0, 2646.0, 2648.0),
            Bar(900, 2648.0, 2650.0, 2640.0, 2645.0),
            Bar(1200, 2645.0, 2652.0, 2644.0, 2649.0),
            Bar(1500, 2649.0, 2655.0, 2646.0, 2650.0),
        ]
        bars_m1 = [
            Bar(1440, 2645.5, 2646.0, 2644.5, 2645.0),
            Bar(1500, 2645.0, 2645.5, 2644.0, current_close)
        ]
        return [], bars_m3, bars_m1

    async def test_approaching_setup_does_not_place_limit_order(self):
        """
        When price is far from entry (> recommended_entry + buffer),
        NO limit order should be dispatched to broker/MT5.
        Instead, candidate_setup is stored on the server.
        """
        # Recommended entry will be 2644.0. With buffer 1.0, touch boundary is 2645.0.
        # Support high is 2645.0. Proximity is 1.5 (zone spans up to 2646.5).
        # Current close is 2646.2 (within S/R proximity so TST triggers, but > 2645.0 so entry not touched yet).
        _, bars_m3, bars_m1 = self._build_bars(current_close=2646.2)

        trade = await self.evaluate_entry.execute(self.session_config, [], bars_m3, bars_m1, [])

        # Server does NOT enter trade immediately
        self.assertIsNone(trade)
        # Broker MUST NOT have received any order (NO LIMIT order placed)
        self.assertEqual(len(self.broker_spy.orders), 0)
        # Server must track candidate setup
        self.assertIsNotNone(self.evaluate_entry.candidate_setup)
        self.assertEqual(self.evaluate_entry.candidate_setup["setup_name"], "TST")
        self.assertEqual(self.evaluate_entry.candidate_setup["entry_price"], 2644.0)

    async def test_price_touch_triggers_market_order_and_in_position(self):
        """
        When live price touches entry (current_price <= recommended_entry + buffer),
        server triggers MARKET order, placing 2 distinct orders on MT5 for Part 1 & Part 2.
        """
        # Current close is 2644.5 (touching the 2644.0 + 1.0 = 2645.0 entry zone)
        _, bars_m3, bars_m1 = self._build_bars(current_close=2644.5)

        trade = await self.evaluate_entry.execute(self.session_config, [], bars_m3, bars_m1, [])

        self.assertIsNotNone(trade)
        self.assertEqual(trade.state, PositionState.IN_POSITION)
        # Broker received MARKET orders!
        self.assertEqual(len(self.broker_spy.orders), 2)
        p1_order = self.broker_spy.orders[0]
        p2_order = self.broker_spy.orders[1]

        self.assertEqual(p1_order["order_type"], "MARKET")
        self.assertEqual(p1_order["volume"], 0.01)
        self.assertIn("P1", p1_order["comment"])

        self.assertEqual(p2_order["order_type"], "MARKET")
        self.assertEqual(p2_order["volume"], 0.01)
        self.assertIn("P2", p2_order["comment"])

        # Tickets assigned accurately
        self.assertEqual(trade.part1.ticket, p1_order["ticket"])
        self.assertEqual(trade.part2.ticket, p2_order["ticket"])

    async def test_single_lot_min_volume(self):
        """
        When user specifies fixed lot 0.01, broker receives exactly 1 order of 0.01.
        """
        self.session_config.risk_management["fixed_lot_size"] = 0.01
        _, bars_m3, bars_m1 = self._build_bars(current_close=2644.5)

        trade = await self.evaluate_entry.execute(self.session_config, [], bars_m3, bars_m1, [])

        self.assertIsNotNone(trade)
        # Broker received 1 order of 0.01
        self.assertEqual(len(self.broker_spy.orders), 1)
        self.assertEqual(self.broker_spy.orders[0]["volume"], 0.01)
        self.assertEqual(trade.part1.lot_size, 0.01)
        self.assertEqual(trade.part2.lot_size, 0.0)

    async def test_mt5_broker_never_falsely_triggers_in_position_via_bar_price(self):
        """
        In ManageLifecycle, if connected to MT5 broker and pending order ticket is NOT in get_open_positions,
        the trade MUST NOT be flipped to IN_POSITION simply because bar.low touched entry.
        """
        lifecycle_use_case = ManageLifecycleUseCase(self.broker_spy, self.event_bus)
        pending_trade = TradeLifecycle(
            trade_id="pending_test",
            symbol="XAUUSD",
            setup_type=SetupType.TST,
            side=OrderSide.BUY,
            state=PositionState.PENDING_ENTRY,
            part1=PositionPart(1, 0.01, 2644.0, 2640.0, 2650.0, ticket=999999),
            part2=PositionPart(2, 0.01, 2644.0, 2640.0, 2655.0, ticket=999999),
            open_time=time.time(),
            limit_order_ticket=999999
        )

        # Bar dips below entry price 2644.0
        bar = Bar(1600, 2645.0, 2646.0, 2643.0, 2643.5)
        updated = await lifecycle_use_case.update(pending_trade, bars_m1=[bar], bars_m3=[])

        # For live MT5 broker, it MUST stay PENDING_ENTRY because broker.get_open_positions is empty!
        self.assertEqual(updated.state, PositionState.PENDING_ENTRY)


if __name__ == "__main__":
    unittest.main()
