import unittest
import asyncio
import time
from core.domain.models import Bar, TradeLifecycle, PositionPart, PositionState, OrderSide, SetupType
from core.domain.rules.profit_protector import ProfitProtector
from core.use_cases.execution.manage_lifecycle import ManageLifecycleUseCase
from core.domain.interfaces.broker import IBrokerGateway
from core.domain.interfaces.event_bus import IEventBus
from config import ProfitProtectionConfig

class MockBroker(IBrokerGateway):
    async def connect(self) -> bool: return True
    async def disconnect(self) -> None: pass
    async def get_account_balance(self) -> float: return 10000.0
    async def get_symbol_info(self, symbol: str) -> dict: return {}
    async def get_terminal_status(self) -> dict: return {"connected": True}
    async def get_latest_bars(self, symbol, timeframe, count): return []
    async def place_order(self, *args, **kwargs): return 1001
    async def modify_position(self, ticket, sl=None, tp=None): return True
    async def close_position(self, ticket, volume=None): return True
    async def cancel_order(self, ticket): return True
    async def get_open_positions(self, symbol=None): return []
    async def get_open_orders(self, symbol=None): return []
    async def get_positions(self, symbol=None): return []
    async def get_account_info(self): return {}

class MockEventBus(IEventBus):
    def __init__(self):
        self.events = []
    async def publish(self, topic, message):
        self.events.append((topic, message))
    async def subscribe(self, topic, handler): pass

class TestProfitProtector(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Thiết lập trade mẫu BUY XAUUSD: Entry 2650.00, SL 2646.00 (Risk dist = 4.0 USD), TP1 2654.00 (1R), TP2 2665.00
        self.part1 = PositionPart(part_number=1, lot_size=0.1, entry_price=2650.00, sl_price=2646.00, tp_price=2654.00, ticket=101)
        self.part2 = PositionPart(part_number=2, lot_size=0.1, entry_price=2650.00, sl_price=2646.00, tp_price=2665.00, ticket=102)
        self.trade = TradeLifecycle(
            trade_id="TRADE_TEST_GOLD",
            symbol="XAUUSD",
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            state=PositionState.TRAILING_STOP,
            part1=self.part1,
            part2=self.part2,
            open_time=time.time()
        )
        self.trade.part1.is_closed = True
        self.trade.part1.close_price = 2654.00
        from config import CONFIG
        self._orig_sl_trailing = getattr(CONFIG.profit_protection, "ENABLE_SL_TRAILING", False)
        self._orig_ratchet = getattr(CONFIG.profit_protection, "ENABLE_RATCHET_SL", False)
        self._orig_time_lock = getattr(CONFIG.profit_protection, "ENABLE_TIME_LOCK", False)
        self._orig_candle = getattr(CONFIG.profit_protection, "ENABLE_CANDLESTICK_EXITS", True)
        CONFIG.profit_protection.ENABLE_SL_TRAILING = True
        CONFIG.profit_protection.ENABLE_RATCHET_SL = True
        CONFIG.profit_protection.ENABLE_TIME_LOCK = True
        CONFIG.profit_protection.ENABLE_CANDLESTICK_EXITS = False

    def tearDown(self):
        from config import CONFIG
        CONFIG.profit_protection.ENABLE_SL_TRAILING = self._orig_sl_trailing
        CONFIG.profit_protection.ENABLE_RATCHET_SL = self._orig_ratchet
        CONFIG.profit_protection.ENABLE_TIME_LOCK = self._orig_time_lock
        CONFIG.profit_protection.ENABLE_CANDLESTICK_EXITS = self._orig_candle



    def test_layer1_ratchet_sl(self):
        """Kiểm tra Lớp 1: Dynamic R-Multiple Ratchet SL trên XAUUSD"""
        # Risk = 4.0 USD.
        # Giá đạt 2653.50 -> lãi 3.50 / 4.0 = +0.875R (>= 0.8R) -> Ratchet Level 1 khóa 0.1R = 2650.40
        new_sl_lvl1 = ProfitProtector.evaluate_ratchet_sl(self.trade, curr_price=2653.50)
        self.assertIsNotNone(new_sl_lvl1)
        self.assertEqual(self.trade.profit_protection_level, 1)
        self.assertGreater(new_sl_lvl1, 2650.30)
        self.trade.part2.sl_price = new_sl_lvl1

        # Giá tăng vọt lên 2655.00 -> lãi 5.0 / 4.0 = +1.25R (>= 1.2R) -> Ratchet Level 2 khóa 0.5R = 2652.00
        new_sl_lvl2 = ProfitProtector.evaluate_ratchet_sl(self.trade, curr_price=2655.00)
        self.assertIsNotNone(new_sl_lvl2)
        self.assertEqual(self.trade.profit_protection_level, 2)
        self.assertEqual(new_sl_lvl2, 2652.00)
        self.trade.part2.sl_price = new_sl_lvl2

        # Giá giảm lại 2653.00 -> Ratchet tuyệt đối KHÔNG hạ SL xuống
        no_sl_drop = ProfitProtector.evaluate_ratchet_sl(self.trade, curr_price=2653.00)
        self.assertIsNone(no_sl_drop)
        self.assertEqual(self.trade.part2.sl_price, 2652.00)

    def test_layer2_momentum_guard(self):
        """Kiểm tra Lớp 2: Momentum Reversal Guard thoát sớm trước khi bị quét SL"""
        # Giả lập trade đã đạt peak +1.4R (2655.60)
        self.trade.max_unrealized_r_part2 = 1.40

        # Tạo chuỗi bar M1 với 14 nến ổn định ATR ~ 1.0
        bars_m1 = []
        for i in range(15):
            bars_m1.append(Bar(
                timestamp=1000 + i * 60,
                open=2655.0, high=2656.0, low=2654.5, close=2655.5
            ))

        # Nến hiện tại: Nến sập mạnh (bearish bar) range 2.5 (>= 1.5 * ATR), giá rơi về 2652.50 (retrace > 40%)
        bars_m1.append(Bar(
            timestamp=2000,
            open=2655.0, high=2655.2, low=2652.5, close=2652.5
        ))

        should_exit, reason = ProfitProtector.evaluate_momentum_guard(
            self.trade, bars_m1, curr_price=2652.50
        )
        self.assertTrue(should_exit)
        self.assertIn("MOMENTUM_REVERSAL_GUARD", reason)

    def test_layer3_time_lock(self):
        """Kiểm tra Lớp 3: Adaptive Time-Based Profit Lock"""
        # Part 2 giữ 21 nến M1 (vượt 20 bars) mà giá 2653.50 (+0.875R >= 0.8R)
        self.trade.bars_in_trailing = 21
        new_sl = ProfitProtector.evaluate_time_lock(self.trade, curr_price=2653.50)
        self.assertIsNotNone(new_sl)
        # Khóa tối thiểu 0.5R = 2650.00 + 2.0 = 2652.00
        self.assertEqual(new_sl, 2652.00)

    async def test_manage_lifecycle_full_integration(self):
        """Kiểm tra tích hợp use case ManageLifecycleUseCase với Profit Protection"""
        broker = MockBroker()
        bus = MockEventBus()
        use_case = ManageLifecycleUseCase(broker, bus)

        # Bar M1 đẩy giá lên 2655.00 (+1.25R)
        bar1 = Bar(timestamp=5000, open=2654.0, high=2655.5, low=2653.8, close=2655.0)
        trade_res = await use_case.update(self.trade, bars_m1=[bar1], bars_m3=[])

        # Đã kích hoạt Ratchet SL và nâng SL Part 2 lên 2652.00
        self.assertEqual(trade_res.profit_protection_level, 2)
        self.assertEqual(trade_res.part2.sl_price, 2652.00)
        self.assertGreater(len(trade_res.profit_protection_events), 0)

        # Kiểm tra event telemetry phát ra RATCHET_SL_UPDATED
        event_types = [e[1]["type"] for e in bus.events if "type" in e[1]]
        self.assertIn("RATCHET_SL_UPDATED", event_types)

if __name__ == "__main__":
    unittest.main()
