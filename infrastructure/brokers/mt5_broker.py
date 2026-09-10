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

        # Check AlgoTrading permission in MT5 Terminal
        term_info = mt5.terminal_info()
        if term_info and not term_info.trade_allowed:
            print("\n" + "!" * 75)
            print("  [MT5Broker ⚠️ CẢNH BÁO QUAN TRỌNG]")
            print("  Tính năng 'Algo Trading' đang bị TẮT trên phần mềm MetaTrader 5!")
            print("  -> Vui lòng bấm vào nút 'Algo Trading' (chuyển sang màu Xanh lá cây)")
            print("     trên thanh công cụ của phần mềm MT5 để MT5 cho phép robot đặt lệnh.")
            print("!" * 75 + "\n")

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
            return {"symbol": symbol, "point": 0.00001, "digits": 5, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        if not info:
            return {"symbol": symbol, "point": 0.00001, "digits": 5, "tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01}
        return {
            "symbol": symbol,
            "point": info.point,
            "digits": info.digits,
            "tick_value": info.trade_tick_value or 1.0,
            "volume_min": info.volume_min,
            "volume_step": info.volume_step,
            "ask": info.ask,
            "bid": info.bid,
            "filling_mode": info.filling_mode
        }
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

        # Check symbol info and filling modes
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        filling_mode = info.filling_mode if info else 1

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

        # Select initial filling mode based on broker support
        # filling_mode bitmask: 1 = FOK, 2 = IOC
        candidate_fillings = []
        if action == mt5.TRADE_ACTION_PENDING:
            candidate_fillings = [mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC]
        else:
            if filling_mode & 2:
                candidate_fillings = [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]
            elif filling_mode & 1:
                candidate_fillings = [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]
            else:
                candidate_fillings = [mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC]

        res = None
        for fill_mode in candidate_fillings:
            request = {
                "action": action,
                "symbol": symbol,
                "volume": float(volume),
                "type": mt5_type,
                "price": float(price),
                "sl": float(sl),
                "tp": float(tp),
                "deviation": 20,
                "magic": 2102026,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": fill_mode
            }

            res = mt5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                ticket = res.order or res.deal
                print(f"[MT5Broker] Order placed successfully! Ticket: {ticket} [{side.value} {order_type} vol={volume} price={price}]")
                return ticket
            elif res and res.retcode == 10030:
                # Unsupported filling mode, try next filling mode in candidate list
                continue
            elif res and res.retcode == 10027:
                print("\n[MT5Broker ❌ LỖI 10027] AutoTrading bị tắt! Hãy bật nút 'Algo Trading' màu xanh trên thanh công cụ MT5.")
                return None
            else:
                break

        err = res.comment if res else mt5.last_error()
        retcode = res.retcode if res else "None"
        print(f"[MT5Broker] order_send failed: retcode={retcode}, error={err}")
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

        # 1. Try finding position by ticket
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            # 2. Try finding positions with our magic number
            all_magic_pos = mt5.positions_get(magic=2102026)
            if all_magic_pos:
                pos = all_magic_pos

        if not pos:
            print(f"[MT5Broker] Position ticket {ticket} not found to close.")
            return False

        p = pos[0]
        close_type = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(p.symbol)
        if not tick:
            return False
        price = tick.bid if p.type == mt5.POSITION_TYPE_BUY else tick.ask
        vol = volume or p.volume

        info = mt5.symbol_info(p.symbol)
        filling_mode = info.filling_mode if info else 1
        fill_mode = mt5.ORDER_FILLING_IOC if (filling_mode & 2) else (mt5.ORDER_FILLING_FOK if (filling_mode & 1) else mt5.ORDER_FILLING_RETURN)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": p.ticket,
            "symbol": p.symbol,
            "volume": float(vol),
            "type": close_type,
            "price": price,
            "deviation": 20,
            "magic": 2102026,
            "comment": "YTC_CLOSE",
            "type_filling": fill_mode
        }
        res = mt5.order_send(request)
        success = bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)
        if success:
            print(f"[MT5Broker] Successfully closed position {p.ticket} (vol={vol})")
        else:
            err = res.comment if res else mt5.last_error()
            print(f"[MT5Broker] Failed to close position {p.ticket}: {err}")
        return success

    async def modify_position(self, ticket: int, sl: float, tp: float) -> bool:
        if not self.connected:
            return False
        # 1. Check if it's an open position
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            all_magic = mt5.positions_get(magic=2102026)
            if all_magic:
                pos = all_magic

        if pos:
            p = pos[0]
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": p.ticket,
                "sl": float(sl),
                "tp": float(tp)
            }
            res = mt5.order_send(request)
            return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)

        # 2. Check if it's a pending order (TRADE_ACTION_MODIFY)
        orders = mt5.orders_get(ticket=ticket)
        if orders:
            ord_item = orders[0]
            request = {
                "action": mt5.TRADE_ACTION_MODIFY,
                "order": ord_item.ticket,
                "price": ord_item.price_open,
                "sl": float(sl),
                "tp": float(tp),
                "type_time": ord_item.type_time,
                "type_filling": ord_item.type_filling
            }
            res = mt5.order_send(request)
            return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)

        return False

    async def get_terminal_status(self) -> Dict[str, Any]:
        """Check live connection and AutoTrading status in MT5."""
        if not MT5_AVAILABLE:
            return {
                "broker_type": "MT5",
                "connected": False,
                "trade_allowed": False,
                "account_trade_allowed": False,
                "trade_expert": False,
                "message": "MetaTrader5 Python package not available."
            }

        term_info = mt5.terminal_info() if self.connected else None
        acc_info = mt5.account_info() if self.connected else None

        connected = bool(term_info and term_info.connected)
        terminal_trade_allowed = bool(term_info and term_info.trade_allowed)
        account_trade_allowed = bool(acc_info and acc_info.trade_allowed)
        trade_expert = bool(acc_info and acc_info.trade_expert)
        auto_trading_ready = connected and terminal_trade_allowed and account_trade_allowed and trade_expert

        message = "Sẵn sàng tự động giao dịch (Algo Trading ON)"
        if not connected:
            message = "Chưa kết nối đến phần mềm MT5 Terminal."
        elif not terminal_trade_allowed:
            message = "Nút 'Algo Trading' trên MT5 đang bị TẮT (màu đỏ). Vui lòng bấm bật sang màu xanh!"
        elif not account_trade_allowed or not trade_expert:
            message = "Tài khoản hoặc EA chưa được cấp quyền giao dịch tự động trong MT5 Tools -> Options."

        return {
            "broker_type": "MT5",
            "connected": connected,
            "trade_allowed": terminal_trade_allowed,
            "account_trade_allowed": account_trade_allowed,
            "trade_expert": trade_expert,
            "auto_trading_ready": auto_trading_ready,
            "account_login": acc_info.login if acc_info else None,
            "account_server": acc_info.server if acc_info else None,
            "account_name": acc_info.name if acc_info else None,
            "message": message
        }
