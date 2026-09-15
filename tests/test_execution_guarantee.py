import unittest
import asyncio
import time
from typing import List, Optional, Dict, Any

from core.domain.models import (
    Bar, OrderSide, SetupType, PositionPart, PositionState, TradeLifecycle
)
from core.domain.interfaces.broker import IBrokerGateway
from core.domain.interfaces.event_bus import IEventBus
from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase

class MockEventBus(IEventBus):
    def __init__(self):
        self.events = []

    async def publish(self, topic: str, data: Any) -> None:
        self.events.append((topic, data))

    def subscribe(self, topic: str, handler: Any) -> None:
        pass

class MockBroker(IBrokerGateway):
    def __init__(self, close_success: bool = True):
        self.close_success = close_success
        self.closed_tickets = []
        self.open_positions_list = []
        self.pending_orders_list = []

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def get_account_balance(self) -> float:
        return 10000.0

    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        return {"symbol": symbol, "point": 0.01, "digits": 2, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}

    async def get_latest_bars(self, symbol: str, timeframe: str, count: int) -> List[Bar]:
        return []

    async def place_order(self, symbol: str, side: OrderSide, order_type: str, volume: float, price: float, sl: float, tp: float, comment: str = "") -> Optional[int]:
        return 12345

    async def cancel_order(self, ticket: int) -> bool:
        return True

    async def close_position(self, ticket: int, volume: Optional[float] = None) -> bool:
        self.closed_tickets.append(ticket)
        return self.close_success

    async def modify_position(self, ticket: int, sl: float, tp: float) -> bool:
        return True

    async def get_terminal_status(self) -> Dict[str, Any]:
        return {"connected": True, "trade_allowed": True, "account_trade_allowed": True, "trade_expert": True}

    async def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.open_positions_list

    async def get_pending_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.pending_orders_list

class TestExecutionGuarantee(unittest.TestCase):
    def setUp(self):
        self.event_bus = MockEventBus()

    def _create_active_trade(self, ticket=1001, bars_in_trade=5) -> TradeLifecycle:
        trade = TradeLifecycle(
            trade_id="test_trade_01",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.IN_POSITION,
            part1=PositionPart(1, 0.1, 2600.0, 2595.0, 2610.0, ticket=ticket),
            part2=PositionPart(2, 0.1, 2600.0, 2595.0, 2620.0, ticket=ticket),
            open_time=time.time() - 300,
            m1_bars_in_trade=bars_in_trade,
            last_bar_timestamp=1000.0
        )
        return trade

    def test_scratch_calls_broker_close_and_updates_state(self):
        broker = MockBroker(close_success=True)
        use_case = ManageLifecycleUseCase(broker, self.event_bus)
        trade = self._create_active_trade(ticket=1001, bars_in_trade=8)

        bars_m1 = [
            Bar(timestamp=1000.0, open=2600.0, high=2602.0, low=2599.0, close=2601.0, volume=100),
            Bar(timestamp=1060.0, open=2600.0, high=2600.5, low=2596.0, close=2596.5, volume=200)
        ]
        bars_m3 = [
            Bar(timestamp=900.0, open=2600.0, high=2603.0, low=2599.0, close=2600.5, volume=300),
            Bar(timestamp=1080.0, open=2600.5, high=2601.0, low=2596.0, close=2596.5, volume=400)
        ]

        updated = asyncio.run(use_case.update(trade, bars_m1, bars_m3, scratch_timeout_bars=8, min_holding_bars=3))

        self.assertIn(1001, broker.closed_tickets, "broker.close_position MUST be called on scratch!")
        self.assertTrue(updated.part1.is_closed)
        self.assertTrue(updated.part2.is_closed)
        self.assertEqual(updated.state, PositionState.SCRATCHED)

    def test_scratch_fails_closed_when_broker_fails(self):
        broker = MockBroker(close_success=False)
        use_case = ManageLifecycleUseCase(broker, self.event_bus)
        trade = self._create_active_trade(ticket=1001, bars_in_trade=8)

        bars_m1 = [
            Bar(timestamp=1000.0, open=2600.0, high=2602.0, low=2599.0, close=2601.0, volume=100),
            Bar(timestamp=1060.0, open=2600.0, high=2600.5, low=2596.0, close=2596.5, volume=200)
        ]
        bars_m3 = [
            Bar(timestamp=900.0, open=2600.0, high=2603.0, low=2599.0, close=2600.5, volume=300),
            Bar(timestamp=1080.0, open=2600.5, high=2601.0, low=2596.0, close=2596.5, volume=400)
        ]

        updated = asyncio.run(use_case.update(trade, bars_m1, bars_m3, scratch_timeout_bars=8, min_holding_bars=3))

        self.assertIn(1001, broker.closed_tickets)
        self.assertEqual(updated.state, PositionState.IN_POSITION)
        self.assertFalse(updated.part1.is_closed)

    def test_reconciliation_liquidates_orphan_position(self):
        from run import SystemOrchestrator

        app = SystemOrchestrator(symbol="XAUUSD", mode="paper")
        broker = MockBroker(close_success=True)
        broker.open_positions_list = [
            {
                "ticket": 99999,
                "identifier": 99999,
                "symbol": "XAUUSD",
                "type": "BUY",
                "volume": 0.2,
                "magic": 2102026,
                "comment": "orphan_trade"
            }
        ]
        app.broker = broker
        app.state["active_trades"] = []
        app._last_reconcile_time = 0.0

        asyncio.run(app.reconcile_positions_watchdog())

        self.assertIn(99999, broker.closed_tickets, "Reconciliation watchdog must liquidate orphan MT5 position!")

    def test_reconciliation_syncs_external_close(self):
        from run import SystemOrchestrator

        app = SystemOrchestrator(symbol="XAUUSD", mode="paper")
        broker = MockBroker()
        broker.open_positions_list = []
        app.broker = broker

        active_trade = self._create_active_trade(ticket=77777, bars_in_trade=5)
        app.state["active_trades"] = [active_trade]
        app._last_reconcile_time = 0.0

        asyncio.run(app.reconcile_positions_watchdog())

        self.assertEqual(active_trade.state, PositionState.FULLY_CLOSED)
        self.assertEqual(active_trade.close_context.get("close_reason"), "EXTERNAL_MT5_SLTP_CLOSED")

if __name__ == "__main__":
    unittest.main()
