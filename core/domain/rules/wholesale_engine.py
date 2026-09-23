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
        min_rr_ratio: float = 1.0,
        min_sl_distance: float = 0.0,  # Safe minimum SL floor to prevent spread stop-outs
        sl_multiplier: float = 1.0,   # e.g. 1.20 = 120% wider SL for noise tolerance
        tp_multiplier: float = 1.0,   # e.g. 0.90 = 90% narrower TP for higher win rate
        adaptive_entry: bool = False,
        min_profit_distance: float = 0.0,
        recent_candle_range: float = 0.0,       # Biên độ nến M1 gần nhất (high - low)
        recent_candle_low: Optional[float] = None,   # Đáy nến gần nhất (BUY SL neo dưới đây)
        recent_candle_high: Optional[float] = None,  # Đỉnh nến gần nhất (SELL SL neo trên đây)
        candle_buffer_ratio: float = 0.25        # Tỷ lệ đệm vượt ngoài đáy/đỉnh nến gần nhất
    ) -> WholesaleCalculation:
        """
        Calculates S1, T1, T2, LWP, LRP according to Section 2.2:
          - S1: Long = Low(Pullback) - Buffer; Short = High(Pullback) + Buffer
          - Dynamic SL Floor: Clamps S1 to at least min_sl_distance from entry
          - SL Multiplier: Scales SL distance (e.g. 1.20 = 120% wider buffer against noise)
          - TP Multiplier: Scales TP distance (e.g. 0.90 = 90% narrower to lock in gains early)
          - T1: Nearest TTF opposing swing boundary (scaled by tp_multiplier, clamped to >= min_profit_distance)
          - T2: Next major HTF S/R boundary (scaled by tp_multiplier)
          - LWP: Extreme breakout trigger of the 1m stall/spring structure
          - LRP: Boundary preserving R:R >= min_rr_ratio for Part 1: (T1 + min_rr_ratio * S1) / (1 + min_rr_ratio)
          - Candle Range SL: SL expands to cover recent candle range × candle_sl_multiplier, preventing stop-hunts
        """
        # --- Candle Range SL Floor (chống quét SL do râu nến retest) ---
        # Tính sàn khoảng cách SL động dựa trên biên độ nến gần nhất
        candle_sl_floor = recent_candle_range * sl_multiplier if recent_candle_range > 0 else 0.0
        effective_min_sl = max(min_sl_distance, candle_sl_floor)

        # Đệm vượt ngoài đáy/đỉnh nến gần nhất (thay thế cho buffer_pts cố định)
        if recent_candle_range > 0:
            candle_extra_buffer = recent_candle_range * candle_buffer_ratio
        else:
            candle_extra_buffer = buffer_pts

        if side == OrderSide.BUY:
            stall_span = max(micro_stall_high - micro_stall_low, 0.0)
            if adaptive_entry and stall_span > 0:
                candidate_entry = micro_stall_low + stall_span * 0.40
                s1_raw = pullback_swing_price - buffer_pts
                base_risk_cand = max(candidate_entry - s1_raw, 0.00001)
                scaled_risk_cand = base_risk_cand * sl_multiplier
                min_risk_from_cand = effective_min_sl + (candidate_entry - micro_stall_low)
                if effective_min_sl > 0 and scaled_risk_cand < min_risk_from_cand:
                    scaled_risk_cand = min_risk_from_cand
                s1_cand = candidate_entry - scaled_risk_cand
                cand_reward_t1 = max(max(t1_price - candidate_entry, 0.0) * tp_multiplier, min_profit_distance)
                t1_cand = candidate_entry + cand_reward_t1
                rr_cand = (t1_cand - candidate_entry) / max(candidate_entry - s1_cand, 0.00001)
                if rr_cand >= min_rr_ratio:
                    recommended_entry = candidate_entry
                else:
                    recommended_entry = micro_stall_low
            else:
                recommended_entry = micro_stall_low  # Wholesale Limit entry at bottom of stall

            # SL = min dưới pullback swing và đáy nến gần nhất, rồi trừ thêm candle_extra_buffer
            sl_anchor = pullback_swing_price
            if recent_candle_low is not None:
                sl_anchor = min(sl_anchor, recent_candle_low)
            s1_raw = sl_anchor - candle_extra_buffer

            base_risk_dist = max(recommended_entry - s1_raw, 0.00001)
            scaled_risk_dist = base_risk_dist * sl_multiplier
            if effective_min_sl > 0 and scaled_risk_dist < effective_min_sl:
                scaled_risk_dist = effective_min_sl
            s1 = recommended_entry - scaled_risk_dist

            # Scale T1 and T2 by tp_multiplier (clamped to at least min_profit_distance)
            base_reward_t1 = max(t1_price - recommended_entry, 0.0)
            scaled_reward_t1 = base_reward_t1 * tp_multiplier
            if min_profit_distance > 0 and scaled_reward_t1 < min_profit_distance:
                scaled_reward_t1 = min_profit_distance
            t1 = recommended_entry + scaled_reward_t1

            base_reward_t2 = max(t2_price - recommended_entry, 0.0)
            scaled_reward_t2 = base_reward_t2 * tp_multiplier
            t2 = recommended_entry + scaled_reward_t2

            lwp = micro_stall_high  # Breakout trigger of 1m stall
            # LRP for Long preserving R:R >= min_rr_ratio
            if min_rr_ratio > 0:
                lrp = (t1 + min_rr_ratio * s1) / (1.0 + min_rr_ratio)
            else:
                lrp = (s1 + t1) / 2.0
            
            risk_dist = max(recommended_entry - s1, 0.00001)
            reward_dist = max(t1 - recommended_entry, 0.0)
            rr_ratio = reward_dist / risk_dist
            
            is_valid = (recommended_entry <= min(lwp, lrp)) and (rr_ratio >= min_rr_ratio)
        else:
            stall_span = max(micro_stall_high - micro_stall_low, 0.0)
            if adaptive_entry and stall_span > 0:
                candidate_entry = micro_stall_high - stall_span * 0.40
                s1_raw = pullback_swing_price + buffer_pts
                base_risk_cand = max(s1_raw - candidate_entry, 0.00001)
                scaled_risk_cand = base_risk_cand * sl_multiplier
                min_risk_from_cand = effective_min_sl + (micro_stall_high - candidate_entry)
                if effective_min_sl > 0 and scaled_risk_cand < min_risk_from_cand:
                    scaled_risk_cand = min_risk_from_cand
                s1_cand = candidate_entry + scaled_risk_cand
                cand_reward_t1 = max(max(candidate_entry - t1_price, 0.0) * tp_multiplier, min_profit_distance)
                t1_cand = candidate_entry - cand_reward_t1
                rr_cand = (candidate_entry - t1_cand) / max(s1_cand - candidate_entry, 0.00001)
                if rr_cand >= min_rr_ratio:
                    recommended_entry = candidate_entry
                else:
                    recommended_entry = micro_stall_high
            else:
                recommended_entry = micro_stall_high  # Wholesale Limit entry at top of stall

            # SL = max trên pullback swing và đỉnh nến gần nhất, rồi cộng thêm candle_extra_buffer
            sl_anchor = pullback_swing_price
            if recent_candle_high is not None:
                sl_anchor = max(sl_anchor, recent_candle_high)
            s1_raw = sl_anchor + candle_extra_buffer

            base_risk_dist = max(s1_raw - recommended_entry, 0.00001)
            scaled_risk_dist = base_risk_dist * sl_multiplier
            if effective_min_sl > 0 and scaled_risk_dist < effective_min_sl:
                scaled_risk_dist = effective_min_sl
            s1 = recommended_entry + scaled_risk_dist

            # Scale T1 and T2 by tp_multiplier (clamped to at least min_profit_distance)
            base_reward_t1 = max(recommended_entry - t1_price, 0.0)
            scaled_reward_t1 = base_reward_t1 * tp_multiplier
            if min_profit_distance > 0 and scaled_reward_t1 < min_profit_distance:
                scaled_reward_t1 = min_profit_distance
            t1 = recommended_entry - scaled_reward_t1

            base_reward_t2 = max(recommended_entry - t2_price, 0.0)
            scaled_reward_t2 = base_reward_t2 * tp_multiplier
            t2 = recommended_entry - scaled_reward_t2

            lwp = micro_stall_low   # Breakdown trigger of 1m stall
            # LRP for Short preserving R:R >= min_rr_ratio
            if min_rr_ratio > 0:
                lrp = (t1 + min_rr_ratio * s1) / (1.0 + min_rr_ratio)
            else:
                lrp = (s1 + t1) / 2.0

            risk_dist = max(s1 - recommended_entry, 0.00001)
            reward_dist = max(recommended_entry - t1, 0.0)
            rr_ratio = reward_dist / risk_dist

            is_valid = (recommended_entry >= max(lwp, lrp)) and (rr_ratio >= min_rr_ratio)

        return WholesaleCalculation(
            setup_type=setup_type,
            side=side,
            S1=round(s1, 5),
            T1=round(t1, 5),
            T2=round(t2, 5),
            LWP=round(lwp, 5),
            LRP=round(lrp, 5),
            is_valid_entry=is_valid,
            recommended_entry=round(recommended_entry, 5),
            rr_ratio_part1=round(rr_ratio, 2),
            min_rr_ratio=min_rr_ratio,
            sl_multiplier=sl_multiplier,
            tp_multiplier=tp_multiplier
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
