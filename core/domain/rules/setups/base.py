"""
Base Setup Strategy Class
"""
from abc import ABC, abstractmethod
from typing import Optional, List, Tuple, Any
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

    @staticmethod
    def is_setup_compatible(
        setup_type: SetupType,
        side: OrderSide,
        market_regime: Any,
        trend: str
    ) -> Tuple[bool, str]:
        """
        Regime & Trend Gating Matrix:
        Ensures setups strictly align with the macro environment and prevents
        catastrophic counter-trend fading during strong momentum or breakout expansions.
        """
        regime_str = getattr(market_regime, "value", str(market_regime)).upper()
        trend_str = str(trend).upper()

        if "TRENDING" in regime_str or "BREAKOUT" in regime_str:
            # 1. Prohibit trading against the prevailing trend
            if "UP" in trend_str and side == OrderSide.SELL:
                return False, f"Counter-trend SELL strictly forbidden in {regime_str} UPTREND"
            if "DOWN" in trend_str and side == OrderSide.BUY:
                return False, f"Counter-trend BUY strictly forbidden in {regime_str} DOWNTREND"

            # 2. Prohibit range-bound setups during trend/breakout expansions
            if setup_type in [SetupType.TST, SetupType.BOF]:
                return False, f"Setup {setup_type.value} strictly forbidden during {regime_str} (Trend continuation PB/BPB/CPB only)"

        return True, "COMPATIBLE"


