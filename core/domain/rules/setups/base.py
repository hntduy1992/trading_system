"""
Base Setup Strategy Class
"""
from abc import ABC, abstractmethod
from typing import Optional, List, Tuple
from core.domain.models import Bar, SwingNode, HTFZone, SetupType, OrderSide, InstrumentProfile

class BaseSetup(ABC):
    def __init__(self, setup_type: SetupType):
        self.setup_type = setup_type

    @abstractmethod
    def evaluate(
        self,
        trend: str,
        swings_3m: List[SwingNode],
        bars_1m: List[Bar],
        resistance_zones: List[HTFZone],
        support_zones: List[HTFZone],
        profile: Optional[InstrumentProfile] = None
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], Optional[float]]:
        """
        Evaluates whether this setup condition is met.
        Returns: (is_triggered, order_side, pullback_swing_price, t1_price, t2_price)
        """
        pass

