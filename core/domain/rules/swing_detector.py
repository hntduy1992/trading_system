"""
Swing Detector & Trend Structure Analyzer
Implements 5-bar swing logic and YTC Trend Evaluation (Section 2.1)
"""
from typing import List, Optional, Tuple
from core.domain.models import Bar, SwingNode, SwingType, HTFZone, Significance

class SwingDetector:
    @staticmethod
    def detect_swings(bars: List[Bar]) -> List[SwingNode]:
        """
        Calculates Swing High (SH) and Swing Low (SL) on TTF (M3) bars.
        Bar C where:
          - SH: High(C) > max(High(A), High(B)) and High(C) > max(High(D), High(E))
          - SL: Low(C) < min(Low(A), Low(B)) and Low(C) < min(Low(D), Low(E))
        """
        swings: List[SwingNode] = []
        if len(bars) < 5:
            return swings

        for i in range(2, len(bars) - 2):
            bar_a = bars[i - 2]
            bar_b = bars[i - 1]
            bar_c = bars[i]
            bar_d = bars[i + 1]
            bar_e = bars[i + 2]

            # Check Swing High
            is_sh = (bar_c.high > max(bar_a.high, bar_b.high)) and (bar_c.high > max(bar_d.high, bar_e.high))
            if is_sh:
                swings.append(SwingNode(
                    swing_type=SwingType.SWING_HIGH,
                    price=bar_c.high,
                    time=bar_c.timestamp,
                    bar_index=i,
                    bar=bar_c
                ))

            # Check Swing Low
            is_sl = (bar_c.low < min(bar_a.low, bar_b.low)) and (bar_c.low < min(bar_d.low, bar_e.low))
            if is_sl:
                swings.append(SwingNode(
                    swing_type=SwingType.SWING_LOW,
                    price=bar_c.low,
                    time=bar_c.timestamp,
                    bar_index=i,
                    bar=bar_c
                ))

        return swings

    @staticmethod
    def evaluate_trend(swings: List[SwingNode], current_price: float) -> str:
        """
        Evaluates trend based on YTC Price Action rules:
          - Uptrend: Sequence of HH and HL. Invalidation: Close below SL preceding highest SH.
          - Downtrend: Sequence of LH and LL. Invalidation: Close above SH preceding lowest SL.
          - Sideways: 4 turning points enclosed within previous swing bounds.
        """
        if len(swings) < 4:
            return "UNDETERMINED"

        recent_highs = [s for s in swings if s.swing_type == SwingType.SWING_HIGH][-3:]
        recent_lows = [s for s in swings if s.swing_type == SwingType.SWING_LOW][-3:]

        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            hh = recent_highs[-1].price > recent_highs[-2].price
            hl = recent_lows[-1].price > recent_lows[-2].price
            lh = recent_highs[-1].price < recent_highs[-2].price
            ll = recent_lows[-1].price < recent_lows[-2].price

            # Check Uptrend
            if hh and hl:
                # Invalidation check: price below SL preceding highest SH
                invalidation_level = recent_lows[-1].price
                if current_price < invalidation_level:
                    return "UPTREND_INVALIDATED"
                return "UPTREND"

            # Check Downtrend
            if lh and ll:
                # Invalidation check: price above SH preceding lowest SL
                invalidation_level = recent_highs[-1].price
                if current_price > invalidation_level:
                    return "DOWNTREND_INVALIDATED"
                return "DOWNTREND"

        # Check Sideways: 4 turning points enclosed within previous swing bounds
        if len(swings) >= 4:
            last_4 = swings[-4:]
            max_bound = max(s.price for s in last_4)
            min_bound = min(s.price for s in last_4)
            if (max_bound - min_bound) > 0:
                return "SIDEWAYS"

        return "CHOPPY"

    @staticmethod
    def parse_htf_zones(htf_bars: List[Bar]) -> Tuple[List[HTFZone], List[HTFZone]]:
        """
        HTF (30-minute / M30) Zone extraction:
          - Resistance_Zone = [Swing_High.Low_Body, Swing_High.High_Wick]
          - Support_Zone = [Swing_Low.Low_Wick, Swing_Low.High_Body]
        """
        swings = SwingDetector.detect_swings(htf_bars)
        resistances: List[HTFZone] = []
        supports: List[HTFZone] = []

        for idx, s in enumerate(swings):
            if s.swing_type == SwingType.SWING_HIGH:
                resistances.append(HTFZone(
                    id=f"res_{idx}",
                    low=s.bar.low_body,
                    high=s.bar.high_wick,
                    significance=Significance.MAJOR,
                    zone_type="RESISTANCE"
                ))
            elif s.swing_type == SwingType.SWING_LOW:
                supports.append(HTFZone(
                    id=f"sup_{idx}",
                    low=s.bar.low_wick,
                    high=s.bar.high_body,
                    significance=Significance.MAJOR,
                    zone_type="SUPPORT"
                ))

        return resistances, supports
