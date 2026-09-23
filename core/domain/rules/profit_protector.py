"""
Profit Protection Engine (Part 2 Active Protection)
Chuyên biệt cho XAUUSD (Gold) Price Action

3 Lớp Bảo Vệ:
  Lớp 1: Dynamic R-Multiple Ratchet Stop Loss (Bậc thang nâng SL theo mốc R).
  Lớp 2: Momentum Reversal Guard (Thoát thị trường Part 2 khi xuất hiện xung lực đảo chiều mạnh).
  Lớp 3: Adaptive Time-Based Profit Lock (Khóa lợi nhuận khi giá chững lại quá lâu).
Tích hợp: Tự động ghi chép dữ liệu và trích xuất LessonRule / structured lesson để học hỏi tối ưu.
"""
from typing import List, Optional, Tuple, Dict, Any
import time
from core.domain.models import Bar, TradeLifecycle, OrderSide, PositionState, get_instrument_profile
from core.domain.rules.vector_dynamics import MicroPatternDetector
from config import CONFIG, ProfitProtectionConfig

class ProfitProtector:
    """
    Quản lý việc bảo vệ lợi nhuận cho vị thế đang chạy (đặc biệt Part 2 sau khi Part 1 đã chốt lời tại T1).
    """

    @staticmethod
    def calculate_unrealized_r(trade: TradeLifecycle, curr_price: float) -> Tuple[float, float, float]:
        """
        Tính toán khoảng cách giá, R hiện tại và risk_dist ban đầu của Part 1.
        Returns: (unrealized_r, profit_dist, risk_dist)
        """
        risk_dist = abs(trade.part1.entry_price - trade.part1.sl_price)
        if risk_dist <= 0:
            return 0.0, 0.0, 0.0

        if trade.side == OrderSide.BUY:
            profit_dist = curr_price - trade.part2.entry_price
        else:
            profit_dist = trade.part2.entry_price - curr_price

        unrealized_r = profit_dist / risk_dist
        return round(unrealized_r, 3), round(profit_dist, 3), round(risk_dist, 3)

    @staticmethod
    def calculate_safe_breathing_distance(
        bars_m1: Optional[List[Bar]] = None,
        profile: Optional[Any] = None,
        min_distance: float = 1.0
    ) -> float:
        """
        Tính toán khoảng đệm thở an toàn (D_safe) dựa trên độ biến động ATR(14) nến M1.
        Đảm bảo không bao giờ dời SL quá gần giá thị trường làm lệnh bị quét non.
        """
        atr = 0.0
        if bars_m1 and len(bars_m1) >= 2:
            atr = MicroPatternDetector.calculate_atr(bars_m1, period=14)
        base_min = getattr(profile, "min_buffer_points", min_distance) if profile else min_distance
        return max(atr * 1.0, base_min, min_distance)

    @staticmethod
    def evaluate_ratchet_sl(
        trade: TradeLifecycle,
        curr_price: float,
        cfg: Optional[ProfitProtectionConfig] = None,
        bars_m1: Optional[List[Bar]] = None,
        safe_cushion: Optional[float] = None
    ) -> Optional[float]:
        """
        Lớp 1: Dynamic R-Multiple Ratchet Stop Loss.
        Kiểm tra nếu unrealized_r đạt các ngưỡng bậc thang thì nâng SL Part 2.
        Đảm bảo nguyên tắc Ratchet: SL chỉ nâng lên theo hướng có lợi, KHÔNG BAO GIỜ hạ xuống.
        Bổ sung cơ chế Pullback Immunity: Tuyệt đối không nâng SL khi giá đang hồi về quá gần mốc dời.
        Returns: new_sl (nếu cần điều chỉnh) hoặc None.
        """
        if cfg is None:
            cfg = getattr(CONFIG, "profit_protection", ProfitProtectionConfig())

        if not cfg.ENABLED:
            return None

        unrealized_r, profit_dist, risk_dist = ProfitProtector.calculate_unrealized_r(trade, curr_price)
        if risk_dist <= 0:
            return None

        # Cập nhật peak R
        if unrealized_r > trade.max_unrealized_r_part2:
            trade.max_unrealized_r_part2 = unrealized_r

        profile = get_instrument_profile(trade.symbol)
        candidate_sl: Optional[float] = None
        new_level = trade.profit_protection_level

        # Duyệt qua các mốc ratchet từ cao xuống thấp
        sorted_levels = sorted(cfg.RATCHET_LEVELS, key=lambda x: x["min_r"], reverse=True)
        for lvl in sorted_levels:
            if trade.max_unrealized_r_part2 >= lvl["min_r"] and lvl["level"] > trade.profit_protection_level:
                lock_r = lvl["lock_r"]
                lock_dist = lock_r * risk_dist
                
                if trade.side == OrderSide.BUY:
                    target_sl = trade.part2.entry_price + lock_dist
                    if target_sl > trade.part2.sl_price:
                        candidate_sl = round(target_sl, profile.digits)
                        new_level = lvl["level"]
                        break
                else:
                    target_sl = trade.part2.entry_price - lock_dist
                    if target_sl < trade.part2.sl_price:
                        candidate_sl = round(target_sl, profile.digits)
                        new_level = lvl["level"]
                        break

        if candidate_sl is not None:
            # PULLBACK IMMUNITY (Chống nến quay đầu):
            # Tính khoảng đệm an toàn D_safe từ nến M1
            if safe_cushion is not None:
                d_safe = safe_cushion
            elif bars_m1:
                d_safe = ProfitProtector.calculate_safe_breathing_distance(bars_m1, profile)
            else:
                d_safe = getattr(profile, "min_buffer_points", 0.8)

            # Nếu giá thị trường đang hồi về quá gần candidate_sl (dưới D_safe),
            # TUYỆT ĐỐI KHÔNG siết SL lên lúc này vì sẽ bị dính râu nến quay đầu.
            if trade.side == OrderSide.BUY:
                if (curr_price - candidate_sl) < d_safe:
                    return None
            else:
                if (candidate_sl - curr_price) < d_safe:
                    return None

            trade.profit_protection_level = new_level
            return candidate_sl

        return None

    @staticmethod
    def evaluate_momentum_guard(
        trade: TradeLifecycle,
        bars_m1: List[Bar],
        curr_price: float,
        cfg: Optional[ProfitProtectionConfig] = None
    ) -> Tuple[bool, str]:
        """
        Lớp 2: Momentum Reversal Guard.
        Phát hiện sự đảo chiều xung lực dữ dội ở M1 khi Part 2 đã có lợi nhuận đáng kể.
        Thay vì chờ giá quét về Trailing SL, chủ động thoát ngay ở giá hiện tại để giữ lại lợi nhuận.
        """
        if cfg is None:
            cfg = getattr(CONFIG, "profit_protection", ProfitProtectionConfig())

        if not cfg.ENABLED or not cfg.ENABLE_MOMENTUM_GUARD:
            return False, ""

        if trade.max_unrealized_r_part2 < cfg.MIN_PEAK_R_FOR_GUARD:
            return False, ""

        if not bars_m1 or len(bars_m1) < cfg.MOMENTUM_ATR_PERIOD + 2:
            return False, ""

        curr_bar = bars_m1[-1]
        unrealized_r, profit_dist, risk_dist = ProfitProtector.calculate_unrealized_r(trade, curr_price)
        if risk_dist <= 0:
            return False, ""

        peak_r = trade.max_unrealized_r_part2
        if peak_r <= 0:
            return False, ""

        retraced_pct = (peak_r - unrealized_r) / peak_r

        atr_1m = MicroPatternDetector.calculate_atr(bars_m1, period=cfg.MOMENTUM_ATR_PERIOD)
        bar_range = curr_bar.high - curr_bar.low
        is_large_bar = (bar_range >= max(cfg.MOMENTUM_BAR_ATR_MULT * atr_1m, 0.8)) if atr_1m > 0 else False

        if trade.side == OrderSide.BUY:
            is_opp_momentum = curr_bar.close < curr_bar.open and is_large_bar
        else:
            is_opp_momentum = curr_bar.close > curr_bar.open and is_large_bar

        if is_opp_momentum and retraced_pct >= cfg.MOMENTUM_REVERSAL_RETRACE_PCT:
            reason = (
                f"MOMENTUM_REVERSAL_GUARD: Peak was {peak_r:.2f}R, retraced {retraced_pct*100:.1f}% "
                f"(current {unrealized_r:.2f}R) with opposite momentum bar ({bar_range:.2f} >= {cfg.MOMENTUM_BAR_ATR_MULT}*ATR)."
            )
            return True, reason

        return False, ""

    @staticmethod
    def evaluate_time_lock(
        trade: TradeLifecycle,
        curr_price: float,
        cfg: Optional[ProfitProtectionConfig] = None,
        bars_m1: Optional[List[Bar]] = None,
        safe_cushion: Optional[float] = None
    ) -> Optional[float]:
        """
        Lớp 3: Adaptive Time-Based Profit Lock.
        Khi lệnh Part 2 đã ở trạng thái TRAILING_STOP quá lâu (ví dụ >= 20 nến M1) 
        nhưng không thể tiếp cận Target T2, nâng SL lên tối thiểu TIME_LOCK_SECURE_R
        đồng thời tuân thủ khoảng đệm thở an toàn D_safe.
        """
        if cfg is None:
            cfg = getattr(CONFIG, "profit_protection", ProfitProtectionConfig())

        if not cfg.ENABLED or not cfg.ENABLE_TIME_LOCK:
            return None

        if trade.bars_in_trailing < cfg.TIME_LOCK_BARS:
            return None

        unrealized_r, profit_dist, risk_dist = ProfitProtector.calculate_unrealized_r(trade, curr_price)
        if risk_dist <= 0:
            return None

        if unrealized_r < cfg.TIME_LOCK_MIN_R:
            return None

        profile = get_instrument_profile(trade.symbol)
        secure_dist = cfg.TIME_LOCK_SECURE_R * risk_dist

        if safe_cushion is not None:
            d_safe = safe_cushion
        elif bars_m1:
            d_safe = ProfitProtector.calculate_safe_breathing_distance(bars_m1, profile)
        else:
            d_safe = getattr(profile, "min_buffer_points", 0.8)

        if trade.side == OrderSide.BUY:
            secure_sl = round(trade.part2.entry_price + secure_dist, profile.digits)
            if secure_sl > trade.part2.sl_price and (curr_price - secure_sl) >= d_safe:
                return secure_sl
        else:
            secure_sl = round(trade.part2.entry_price - secure_dist, profile.digits)
            if secure_sl < trade.part2.sl_price and (secure_sl - curr_price) >= d_safe:
                return secure_sl

        return None

    @staticmethod
    def build_trading_experience_lesson(
        trade: TradeLifecycle,
        trigger_type: str,
        reason: str,
        bars_m1: List[Bar],
        bars_m3: List[Bar],
        market_regime: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Ghi chép kinh nghiệm giao dịch kết hợp các điều kiện môi trường thị trường
        để truyền sang Lesson Compiler và AI Audit tự động tối ưu hóa.
        """
        curr_bar = bars_m1[-1] if bars_m1 else None
        atr_1m = MicroPatternDetector.calculate_atr(bars_m1, period=14) if bars_m1 else 0.0

        setup_name = trade.setup_type.value if hasattr(trade.setup_type, "value") else str(trade.setup_type)
        side_str = trade.side.value if hasattr(trade.side, "value") else str(trade.side)

        lesson_record = {
            "timestamp": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "trade_id": trade.trade_id,
            "symbol": trade.symbol,
            "setup": setup_name,
            "side": side_str,
            "trigger_type": trigger_type,   # "RATCHET_LOCK" | "MOMENTUM_GUARD" | "TIME_LOCK"
            "reason": reason,
            "bars_in_trailing": trade.bars_in_trailing,
            "max_unrealized_r": trade.max_unrealized_r_part2,
            "exit_or_sl_price": trade.part2.sl_price,
            "market_conditions": {
                "regime": market_regime or "UNKNOWN",
                "atr_1m": round(atr_1m, 3),
                "last_bar_range": round(curr_bar.range, 3) if curr_bar else 0.0,
                "is_gold": "XAU" in trade.symbol.upper()
            },
            # Dữ liệu phục vụ Lessons-as-Rules compiler
            "structured_lesson": {
                "condition": {
                    "setup": setup_name,
                    "regime": market_regime or "UNKNOWN",
                    "side": side_str
                },
                "action": "BOOST" if trigger_type in ["RATCHET_LOCK", "TIME_LOCK"] else "PENALIZE",
                "delta": 0.05 if trigger_type == "RATCHET_LOCK" else -0.05,
                "reason": f"[{trade.symbol} Profit Protection] {trigger_type} ({reason}) tai {setup_name} {side_str}"
            }
        }
        return lesson_record
