"""
Wholesale Algorithm & Price Invalidation Engine
Implements Mathematical Entry Calculation (Section 2.2 of YTC Specification)
"""
from typing import Optional, Tuple
from core.domain.models import SetupType, OrderSide, WholesaleCalculation

class WholesaleEngine:
    @staticmethod
    def calculate_wholesale_levels(
        setup_type: SetupType,
        side: OrderSide,
        pullback_swing_price: float,
        t1_price: float,
        t2_price: float,
        micro_stall_high: float,
        micro_stall_low: float,
        buffer_pts: float = 0.0002,  # ~2 pips buffer
        min_rr_ratio: float = 1.0
    ) -> WholesaleCalculation:
        """
        Calculates S1, T1, T2, LWP, LRP according to Section 2.2:
          - S1: Long = Low(Pullback) - Buffer; Short = High(Pullback) + Buffer
          - T1: Nearest TTF opposing swing boundary
          - T2: Next major HTF S/R boundary
          - LWP: Extreme breakout trigger of the 1m stall/spring structure
          - LRP: Boundary preserving R:R >= 1.0 for Part 1: (S1 + T1) / 2
        """
        if side == OrderSide.BUY:
            s1 = pullback_swing_price - buffer_pts
            lwp = micro_stall_high  # Breakout trigger of 1m stall
            # LRP for Long: (S1 + T1) / 2 ensures (T1 - LRP) / (LRP - S1) >= 1.0
            lrp = (s1 + t1_price) / 2.0
            recommended_entry = micro_stall_low  # Wholesale Limit entry at bottom of stall
            is_valid = recommended_entry <= min(lwp, lrp)
        else:
            s1 = pullback_swing_price + buffer_pts
            lwp = micro_stall_low   # Breakdown trigger of 1m stall
            # LRP for Short: (S1 + T1) / 2 ensures (LRP - T1) / (S1 - LRP) >= 1.0
            lrp = (s1 + t1_price) / 2.0
            recommended_entry = micro_stall_high  # Wholesale Limit entry at top of stall
            is_valid = recommended_entry >= max(lwp, lrp)

        return WholesaleCalculation(
            setup_type=setup_type,
            side=side,
            S1=round(s1, 5),
            T1=round(t1_price, 5),
            T2=round(t2_price, 5),
            LWP=round(lwp, 5),
            LRP=round(lrp, 5),
            is_valid_entry=is_valid,
            recommended_entry=round(recommended_entry, 5)
        )

    @staticmethod
    def validate_entry_fill(
        side: OrderSide,
        current_price: float,
        lwp: float,
        lrp: float
    ) -> Tuple[bool, str]:
        """
        Valid Entry Zone Assertion:
          - Long: Price <= min(LWP, LRP)
          - Short: Price >= max(LWP, LRP)
          - If market moves beyond: EMIT CANCEL_EVENT (DO NOT CHASE)
        """
        if side == OrderSide.BUY:
            if current_price > min(lwp, lrp):
                return False, "CANCEL_EVENT: Price exceeded min(LWP, LRP). Do NOT chase."
            return True, "VALID_WHOLESALE_BUY"
        else:
            if current_price < max(lwp, lrp):
                return False, "CANCEL_EVENT: Price dropped below max(LWP, LRP). Do NOT chase."
            return True, "VALID_WHOLESALE_SELL"
