"""
MetaTrader 5 Real-Time Execution Adapter
Triển khai IBrokerGateway kết nối native MT5 Terminal trên Windows
"""
import time
from typing import List, Optional, Dict, Any
from core.domain.interfaces.broker import IBrokerGateway
from core.domain.models import Bar, OrderSide

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

class MT5Broker(IBrokerGateway):
    def __init__(self, login: int = 0, password: str = "", server: str = "", path: str = ""):
        self.login = login
        self.password = password
        self.server = server
        self.path = path
        self.connected = False

    async def connect(self) -> bool:
        if not MT5_AVAILABLE:
            print("[MT5Broker] MetaTrader5 library is not installed.")
            return False

        init_kwargs = {}
        if self.path:
            init_kwargs["path"] = self.path

        if not mt5.initialize(**init_kwargs):
            print(f"[MT5Broker] Initialize failed, error: {mt5.last_error()}")
            return False

        if self.login and self.password and self.server:
            if not mt5.login(login=self.login, password=self.password, server=self.server):
                print(f"[MT5Broker] Login failed, error: {mt5.last_error()}")
                return False

        self.connected = True
        print("[MT5Broker] Successfully connected to MetaTrader 5.")
        return True

    async def disconnect(self) -> None:
        if MT5_AVAILABLE and self.connected:
            mt5.shutdown()
            self.connected = False

    async def get_account_balance(self) -> float:
        if not self.connected:
            return 10000.0
        acc = mt5.account_info()
        return acc.balance if acc else 10000.0

    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        if not self.connected:
            return {"symbol": symbol, "point": 0.00001, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
        info = mt5.symbol_info(symbol)
        if not info:
            return {"symbol": symbol, "point": 0.00001, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
        return {
            "symbol": symbol,
            "point": info.point,
            "digits": info.digits,
            "tick_value": info.trade_tick_value or 1.0,
            "volume_min": info.volume_min,
            "volume_step": info.volume_step
        }

    def _map_tf(self, tf_str: str) -> Any:
        mapping = {
            "M1": mt5.TIMEFRAME_M1,
            "M3": mt5.TIMEFRAME_M3,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1
        }
        return mapping.get(tf_str, mt5.TIMEFRAME_M1)

    async def get_latest_bars(self, symbol: str, timeframe: str, count: int) -> List[Bar]:
        if not self.connected:
            return []
        mt5_tf = self._map_tf(timeframe)
        rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, count)
        if rates is None or len(rates) == 0:
            return []
        bars: List[Bar] = []
        for r in rates:
            bars.append(Bar(
                timestamp=float(r['time']),
                open=float(r['open']),
                high=float(r['high']),
                low=float(r['low']),
                close=float(r['close']),
                volume=float(r['tick_volume']),
                timeframe=timeframe
            ))
        return bars

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
        if not self.connected:
            return None

        # Determine MT5 order action and type
        action = mt5.TRADE_ACTION_PENDING
        mt5_type = mt5.ORDER_TYPE_BUY_LIMIT
        if order_type == "LIMIT":
            mt5_type = mt5.ORDER_TYPE_BUY_LIMIT if side == OrderSide.BUY else mt5.ORDER_TYPE_SELL_LIMIT
        elif order_type == "STOP":
            mt5_type = mt5.ORDER_TYPE_BUY_STOP if side == OrderSide.BUY else mt5.ORDER_TYPE_SELL_STOP
        elif order_type == "MARKET":
            action = mt5.TRADE_ACTION_DEAL
            mt5_type = mt5.ORDER_TYPE_BUY if side == OrderSide.BUY else mt5.ORDER_TYPE_SELL

        request = {
            "action": action,
            "symbol": symbol,
            "volume": float(volume),
            "type": mt5_type,
            "price": float(price),
            "sl": float(sl),
            "tp": float(tp),
            "deviation": 10,
            "magic": 2102026,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC
        }

        res = mt5.order_send(request)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            return res.order
        else:
            err = res.comment if res else mt5.last_error()
            print(f"[MT5Broker] order_send failed: {err}")
            return None

    async def cancel_order(self, ticket: int) -> bool:
        if not self.connected:
            return False
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket
        }
        res = mt5.order_send(request)
        return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)

    async def close_position(self, ticket: int, volume: Optional[float] = None) -> bool:
        if not self.connected:
            return False
        # Get position info to determine close side
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            return False
        p = pos[0]
        close_type = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(p.symbol).bid if p.type == mt5.POSITION_TYPE_BUY else mt5.symbol_info_tick(p.symbol).ask
        vol = volume or p.volume

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": p.symbol,
            "volume": float(vol),
            "type": close_type,
            "price": price,
            "deviation": 10,
            "magic": 2102026,
            "comment": "YTC_CLOSE"
        }
        res = mt5.order_send(request)
        return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)

    async def modify_position(self, ticket: int, sl: float, tp: float) -> bool:
        if not self.connected:
            return False
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": float(sl),
            "tp": float(tp)
        }
        res = mt5.order_send(request)
        return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)
