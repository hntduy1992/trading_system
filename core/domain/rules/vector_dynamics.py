"""
Vector Dynamics & Micro Pattern Detector (LTF M1)
Section 2.1 & 2.2 of YTC Specification
"""
from typing import List, Optional, Tuple
import math
from core.domain.models import Bar, SwingNode, VectorDynamics, HTFZone

class VectorDynamicsCalculator:
    @staticmethod
    def calculate_vector_dynamics(swings: List[SwingNode]) -> Optional[VectorDynamics]:
        """
        Calculates Vector Dynamics between successive swing waves:
          - Momentum = dPrice / dTime
          - Projection = |Extreme_current - Extreme_previous|
          - Depth = (|Retracement| / |Extension|) * 100%
        """
        if len(swings) < 3:
            return None

        s1, s2, s3 = swings[-3], swings[-2], swings[-1]

        # Momentum of the latest leg
        d_price = abs(s3.price - s2.price)
        d_time = max(abs(s3.time - s2.time), 1.0)
        momentum = d_price / d_time

        # Projection: comparing consecutive swing extremes of same type
        # E.g. between High(t) and High(t-1)
        projection = abs(s3.price - s1.price)

        # Depth of retracement
        extension_range = abs(s2.price - s1.price)
        retracement_range = abs(s3.price - s2.price)
        depth = (retracement_range / extension_range * 100.0) if extension_range > 0 else 0.0

        return VectorDynamics(
            momentum=momentum,
            projection=projection,
            depth=depth
        )

class MicroPatternDetector:
    @staticmethod
    def calculate_atr(bars: List[Bar], period: int = 14) -> float:
        if not bars:
            return 1.0  # Safe fallback
        if len(bars) < 2:
            # Dynamic approximation: 0.05% of asset price
            return max(bars[0].close * 0.0005, 0.0001)

        tr_list = []
        for i in range(1, min(len(bars), period + 1)):
            c_prev = bars[-i - 1].close
            curr = bars[-i]
            tr = max(curr.high - curr.low, abs(curr.high - c_prev), abs(curr.low - c_prev))
            tr_list.append(tr)
        return (sum(tr_list) / len(tr_list)) if tr_list else max(bars[-1].close * 0.0005, 0.0001)


    @staticmethod
    def detect_stall(bars_1m: List[Bar], min_candles: int = 3, atr_factor: float = 1.5) -> Tuple[bool, Optional[float], Optional[float]]:
        """
        Stall / Congestion: >= min_candles consecutive bars overlapping within reasonable local ATR consolidation.
        Returns: (is_stall, stall_low, stall_high)
        """
        if len(bars_1m) < min_candles:
            if bars_1m:
                return False, bars_1m[-1].low, bars_1m[-1].high
            return False, None, None

        recent_bars = bars_1m[-min_candles:]
        local_atr = MicroPatternDetector.calculate_atr(bars_1m, period=14)
        max_allowed_range = max(local_atr * atr_factor, 0.0001)

        stall_high = max(b.high for b in recent_bars)
        stall_low = min(b.low for b in recent_bars)
        actual_range = stall_high - stall_low

        is_stall = (actual_range <= max_allowed_range)
        return is_stall, stall_low, stall_high

    @staticmethod
    def detect_spring(bar: Bar, support_level: float) -> bool:
        """
        Spring: Bar spikes below S/R level and immediately closes back above.
        """
        return (bar.low < support_level) and (bar.close > support_level)

    @staticmethod
    def detect_upthrust(bar: Bar, resistance_level: float) -> bool:
        """
        Upthrust: Bar spikes above S/R level and immediately closes back below.
        """
        return (bar.high > resistance_level) and (bar.close < resistance_level)
