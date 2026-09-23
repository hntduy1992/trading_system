import unittest
import asyncio
import time
from core.domain.models import Bar, TradeLifecycle, PositionPart, PositionState, OrderSide, SetupType
from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase
from core.domain.interfaces.broker import IBrokerGateway
from core.domain.interfaces.event_bus import IEventBus
from infrastructure.brokers.paper_broker import PaperBroker

class MockTrackingBroker(IBrokerGateway):
    def __init__(self):
        self.closed_calls = []
        self.open_positions_list = []
        self.open_orders_list = []

    async def connect(self) -> bool: return True
    async def disconnect(self) -> None: pass
    async def get_account_balance(self) -> float: return 10000.0
    async def get_symbol_info(self, symbol: str) -> dict: return {}
    async def get_terminal_status(self) -> dict: return {"connected": True}
    async def get_latest_bars(self, symbol, timeframe, count): return []
    async def place_order(self, *args, **kwargs): return 1001
    async def modify_position(self, ticket, sl=None, tp=None): return True
    async def cancel_order(self, ticket): return True

    async def close_position(self, ticket, volume=None):
        self.closed_calls.append({"ticket": ticket, "volume": volume})
        return True

    async def get_open_positions(self, symbol=None):
        return self.open_positions_list

    async def get_open_orders(self, symbol=None):
        return self.open_orders_list

class MockEventBus(IEventBus):
    def __init__(self):
        self.events = []
    async def publish(self, topic, message):
        self.events.append((topic, message))
    async def subscribe(self, topic, handler): pass

class TestReconciliationAndScratch(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.broker = MockTrackingBroker()
        self.bus = MockEventBus()
        self.use_case = ManageLifecycleUseCase(self.broker, self.bus)

    async def test_scratch_calls_broker_close_position_once_when_same_ticket(self):
        """Kiểm tra: Khi kích hoạt Scratch, broker.close_position PHẢI được gọi đúng 1 lần cho cùng 1 ticket MT5."""
        trade = TradeLifecycle(
            trade_id="ytc_scratch_test",
            symbol="XAUUSD",
            setup_type=SetupType.TST,
            side=OrderSide.SELL,
            state=PositionState.IN_POSITION,
            part1=PositionPart(1, 0.01, 4330.0, 4335.0, 4325.0, ticket=3827066283),
            part2=PositionPart(2, 0.01, 4330.0, 4335.0, 4320.0, ticket=3827066283),
            open_time=time.time(),
            m1_bars_in_trade=8
        )

        bars_m1 = [
            Bar(timestamp=1000 + i * 60, open=4330.0, high=4330.5, low=4329.8, close=4330.1)
            for i in range(10)
        ]

        res_trade = trade
        for i in range(10):
            res_trade = await self.use_case.update(res_trade, bars_m1=bars_m1[:i+1], bars_m3=[], scratch_timeout_bars=8)
            if res_trade.state == PositionState.SCRATCHED:
                break

        self.assertEqual(res_trade.state, PositionState.SCRATCHED)
        self.assertTrue(res_trade.part1.is_closed)
        self.assertTrue(res_trade.part2.is_closed)

        # Broker PHẢI nhận được đúng 1 lệnh close_position cho ticket 3827066283
        self.assertEqual(len(self.broker.closed_calls), 1)
        self.assertEqual(self.broker.closed_calls[0]["ticket"], 3827066283)

    async def test_stop_loss_calls_broker_close_position_with_deduplication(self):
        """Kiểm tra: Khi cắn SL, broker.close_position được gọi đúng và không bị gọi thừa khi dùng chung ticket."""
        trade = TradeLifecycle(
            trade_id="ytc_sl_test",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.IN_POSITION,
            part1=PositionPart(1, 0.01, 4330.0, 4325.0, 4335.0, ticket=999999),
            part2=PositionPart(2, 0.01, 4330.0, 4325.0, 4340.0, ticket=999999),
            open_time=time.time()
        )

        bar_sl = Bar(timestamp=2000, open=4328.0, high=4328.5, low=4324.0, close=4324.5)
        res_trade = await self.use_case.update(trade, bars_m1=[bar_sl], bars_m3=[])

        self.assertEqual(res_trade.state, PositionState.STOPPED_OUT)
        self.assertTrue(res_trade.part1.is_closed)
        self.assertTrue(res_trade.part2.is_closed)
        self.assertEqual(len(self.broker.closed_calls), 1)
        self.assertEqual(self.broker.closed_calls[0]["ticket"], 999999)

    async def test_paper_broker_get_open_positions_and_orders(self):
        """Kiểm tra PaperBroker phản hồi chính xác get_open_positions và get_open_orders."""
        paper = PaperBroker(symbol="XAUUSD")

        t_market = await paper.place_order("XAUUSD", OrderSide.BUY, "MARKET", 0.02, 2650.0, 2645.0, 2655.0)
        t_limit = await paper.place_order("XAUUSD", OrderSide.SELL, "LIMIT", 0.02, 2660.0, 2665.0, 2650.0)

        open_pos = await paper.get_open_positions("XAUUSD")
        open_ords = await paper.get_open_orders("XAUUSD")

        self.assertEqual(len(open_pos), 1)
        self.assertEqual(open_pos[0]["ticket"], t_market)
        self.assertEqual(open_pos[0]["type"], "BUY")

        self.assertEqual(len(open_ords), 1)
        self.assertEqual(open_ords[0]["ticket"], t_limit)
        self.assertEqual(open_ords[0]["type"], "LIMIT")

        await paper.close_position(t_market)
        open_pos_after = await paper.get_open_positions("XAUUSD")
        self.assertEqual(len(open_pos_after), 0)

if __name__ == "__main__":
    unittest.main()
