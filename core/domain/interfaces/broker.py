"""
Broker Interface (Port) - Abstract base for MT5 and Paper Broker
"""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Tuple
from core.domain.models import Bar, OrderSide, PositionPart

class IBrokerGateway(ABC):
    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the broker terminal / service."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect and cleanup connection."""
        pass

    @abstractmethod
    async def get_account_balance(self) -> float:
        """Get current account balance in deposit currency."""
        pass

    @abstractmethod
    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Get tick size, tick value, point size, min lot, lot step."""
        pass

    @abstractmethod
    async def get_latest_bars(self, symbol: str, timeframe: str, count: int) -> List[Bar]:
        """Fetch the most recent closed/forming bars."""
        pass

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: str,  # "LIMIT", "STOP", "MARKET"
        volume: float,
        price: float,
        sl: float,
        tp: float,
        comment: str = ""
    ) -> Optional[int]:
        """Send order to broker. Returns ticket ID if successful."""
        pass

    @abstractmethod
    async def cancel_order(self, ticket: int) -> bool:
        """Cancel an unfilled pending order (TRADE_ACTION_REMOVE)."""
        pass

    @abstractmethod
    async def close_position(self, ticket: int, volume: Optional[float] = None) -> bool:
        """Close an open position at market price (TRADE_ACTION_DEAL)."""
        pass

    @abstractmethod
    async def modify_position(self, ticket: int, sl: float, tp: float) -> bool:
        """Modify SL and TP of an active position (TRADE_ACTION_SLTP)."""
        pass
