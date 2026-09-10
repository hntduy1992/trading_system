"""
In-Memory Paper Broker Simulation Adapter
Enables full backtest, dry-run and development without requiring active MT5 connection
"""
import time
import random
from typing import List, Optional, Dict, Any
from core.domain.interfaces.broker import IBrokerGateway
from core.domain.models import Bar, OrderSide

class PaperBroker(IBrokerGateway):
    def __init__(self, initial_balance: float = 10000.0, symbol: str = "XAUUSD"):
        self.balance = initial_balance
        self.symbol = symbol
        self.orders: Dict[int, Dict[str, Any]] = {}
        self.positions: Dict[int, Dict[str, Any]] = {}
        self._next_ticket = 100001
        
        from core.domain.models import get_instrument_profile
        self.profile = get_instrument_profile(symbol)
        self._current_price = self.profile.base_price
        self._bars_cache: Dict[str, List[Bar]] = {
            "M1": [],
            "M3": [],
            "M30": []
        }
        self._generate_initial_data()

    def _generate_initial_data(self) -> None:
        now = int(time.time())
        m1_now = (now // 60) * 60
        m3_now = (now // 180) * 180
        m30_now = (now // 1800) * 1800

        base_price = self.profile.base_price
        vol_scale = base_price * 0.001

        # Generate 100 M30 bars
        m30_price = base_price
        for i in range(100, 0, -1):
            t = m30_now - i * 1800
            drift = (random.random() - 0.5) * vol_scale * 2.0
            o = round(m30_price, self.profile.digits)
            c = round(o + drift, self.profile.digits)
            h = round(max(o, c) + random.random() * vol_scale * 1.0, self.profile.digits)
            l = round(min(o, c) - random.random() * vol_scale * 1.0, self.profile.digits)
            self._bars_cache["M30"].append(Bar(float(t), o, h, l, c, 1000, "M30"))
            m30_price = c

        # Generate 150 M3 bars
        m3_price = m30_price
        for i in range(150, 0, -1):
            t = m3_now - i * 180
            drift = (random.random() - 0.5) * vol_scale * 0.8
            o = round(m3_price, self.profile.digits)
            c = round(o + drift, self.profile.digits)
            h = round(max(o, c) + random.random() * vol_scale * 0.4, self.profile.digits)
            l = round(min(o, c) - random.random() * vol_scale * 0.4, self.profile.digits)
            self._bars_cache["M3"].append(Bar(float(t), o, h, l, c, 500, "M3"))
            m3_price = c

        # Generate 200 M1 bars
        m1_price = m3_price
        for i in range(200, 0, -1):
            t = m1_now - i * 60
            drift = (random.random() - 0.5) * vol_scale * 0.3
            o = round(m1_price, self.profile.digits)
            c = round(o + drift, self.profile.digits)
            h = round(max(o, c) + random.random() * vol_scale * 0.2, self.profile.digits)
            l = round(min(o, c) - random.random() * vol_scale * 0.2, self.profile.digits)
            self._bars_cache["M1"].append(Bar(float(t), o, h, l, c, 100, "M1"))
            m1_price = c
        self._current_price = m1_price

    def advance_tick(self, price_delta: Optional[float] = None) -> Bar:
        """Simulates incoming real-time market tick/bar with instrument volatility."""
        vol_scale = self.profile.base_price * 0.0003
        if price_delta is None:
            price_delta = (random.random() - 0.5) * vol_scale
        o_price = self._current_price
        c_price = round(o_price + price_delta, self.profile.digits)
        self._current_price = c_price

        now = int(time.time())
        m1_time = (now // 60) * 60

        m1_bars = self._bars_cache["M1"]
        if m1_bars and int(m1_bars[-1].timestamp) == m1_time:
            last = m1_bars[-1]
            last.high = round(max(last.high, c_price), self.profile.digits)
            last.low = round(min(last.low, c_price), self.profile.digits)
            last.close = c_price
            last.volume += 1
            curr_bar = last
        else:
            new_bar = Bar(float(m1_time), o_price, max(o_price, c_price), min(o_price, c_price), c_price, 1, "M1")
            m1_bars.append(new_bar)
            if len(m1_bars) > 300:
                m1_bars.pop(0)
            curr_bar = new_bar

        # Update M3 forming bar
        m3_time = (now // 180) * 180
        m3_bars = self._bars_cache["M3"]
        if m3_bars and int(m3_bars[-1].timestamp) == m3_time:
            last = m3_bars[-1]
            last.high = round(max(last.high, c_price), self.profile.digits)
            last.low = round(min(last.low, c_price), self.profile.digits)
            last.close = c_price
            last.volume += 1
        else:
            new_m3 = Bar(float(m3_time), o_price, max(o_price, c_price), min(o_price, c_price), c_price, 1, "M3")
            m3_bars.append(new_m3)
            if len(m3_bars) > 200:
                m3_bars.pop(0)

        # Update M30 forming bar
        m30_time = (now // 1800) * 1800
        m30_bars = self._bars_cache["M30"]
        if m30_bars and int(m30_bars[-1].timestamp) == m30_time:
            last = m30_bars[-1]
            last.high = round(max(last.high, c_price), self.profile.digits)
            last.low = round(min(last.low, c_price), self.profile.digits)
            last.close = c_price
            last.volume += 1
        else:
            new_m30 = Bar(float(m30_time), o_price, max(o_price, c_price), min(o_price, c_price), c_price, 1, "M30")
            m30_bars.append(new_m30)
            if len(m30_bars) > 150:
                m30_bars.pop(0)

        return curr_bar



    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def get_account_balance(self) -> float:
        return self.balance

    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "point": self.profile.point,
            "digits": self.profile.digits,
            "tick_value": self.profile.tick_value,
            "volume_min": 0.01,
            "volume_step": 0.01
        }


    async def get_latest_bars(self, symbol: str, timeframe: str, count: int) -> List[Bar]:
        bars = self._bars_cache.get(timeframe, [])
        return bars[-count:] if len(bars) >= count else bars

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
        ticket = self._next_ticket
        self._next_ticket += 1

        self.orders[ticket] = {
            "ticket": ticket,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "volume": volume,
            "price": price,
            "sl": sl,
            "tp": tp,
            "comment": comment,
            "status": "OPEN"
        }
        return ticket

    async def cancel_order(self, ticket: int) -> bool:
        if ticket in self.orders:
            del self.orders[ticket]
            return True
        return False

    async def close_position(self, ticket: int, volume: Optional[float] = None) -> bool:
        if ticket in self.orders:
            del self.orders[ticket]
            return True
        return False

    async def modify_position(self, ticket: int, sl: float, tp: float) -> bool:
        if ticket in self.orders:
            self.orders[ticket]["sl"] = sl
            self.orders[ticket]["tp"] = tp
            return True
        return False

    async def get_terminal_status(self) -> Dict[str, Any]:
        return {
            "broker_type": "PaperBroker (Simulation)",
            "connected": True,
            "trade_allowed": True,
            "account_trade_allowed": True,
            "trade_expert": True,
            "auto_trading_ready": True,
            "account_login": "PAPER_DEMO",
            "account_server": "Internal Simulation",
            "account_name": "Virtual Account",
            "message": "Môi trường giả lập (Paper Trading) luôn sẵn sàng tự động vào lệnh."
        }
