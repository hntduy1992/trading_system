"""
Pre-Entry Deterministic Scorer (Layer 2 Gate)
Tính điểm 0.0–1.0 thuần toán học — KHÔNG gọi AI, KHÔNG tốn token.
Là bộ lọc trước khi quyết định có cần escalate lên AI hay không.

Công thức tổng hợp:
  score = 0.35*regime_compat + 0.30*rr_score + 0.20*sl_score + 0.15*wholesale_score
          - macro_penalty - lessons_penalty
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple
from core.domain.rules.lessons_compiler import LessonRule, LessonsCompiler
from core.domain.rules.session_probability import SessionProbabilityLayer
from core.domain.rules.confluence_scorer import ConfluenceScorer, ConfluenceResult

# ---------------------------------------------------------------------------
# Regime × Setup Compatibility Matrix
# Trọng số khả năng phù hợp của từng setup trong từng chế độ thị trường.
# Dựa trên YTC Price Action Matrix (Lance Beggs).
# ---------------------------------------------------------------------------

REGIME_SETUP_MATRIX: Dict[str, Dict[str, float]] = {
    "TRENDING_STEADY": {
        "PB": 1.00, "CPB": 0.90, "BPB": 0.80,
        "TST": 0.20, "BOF": 0.30,
        "TREND_BAR_FAIL": 0.70, "INSIDE_BAR_SMA21": 0.75,
        "ID_NR4": 0.60, "NR7_EMA20": 0.75, "YUM_YUM": 0.80,
    },
    "TRENDING_WEAKENING": {
        "CPB": 0.90, "BOF": 0.80, "TST": 0.50,
        "PB": 0.30, "BPB": 0.40,
        "TREND_BAR_FAIL": 0.80, "INSIDE_BAR_SMA21": 0.50,
        "ID_NR4": 0.65, "NR7_EMA20": 0.55, "YUM_YUM": 0.45,
    },
    "SIDEWAYS_RANGE": {
        "TST": 1.00, "BOF": 0.90, "BPB": 0.30,
        "PB": 0.20, "CPB": 0.20,
        "TREND_BAR_FAIL": 0.60, "INSIDE_BAR_SMA21": 0.40,
        "ID_NR4": 0.85, "NR7_EMA20": 0.45, "YUM_YUM": 0.30,
    },
    "BREAKOUT_EXPANSION": {
        "BPB": 1.00, "BOF": 0.70, "PB": 0.40,
        "TST": 0.30, "CPB": 0.35,
        "YUM_YUM": 0.85, "NR7_EMA20": 0.60,
        "TREND_BAR_FAIL": 0.50, "INSIDE_BAR_SMA21": 0.40, "ID_NR4": 0.50,
    },
    "CHOPPY_NO_TRADE": {
        # Không nên giao dịch trong chế độ này
        "PB": 0.10, "CPB": 0.10, "TST": 0.15, "BOF": 0.15,
        "BPB": 0.10, "TREND_BAR_FAIL": 0.10, "INSIDE_BAR_SMA21": 0.10,
        "ID_NR4": 0.10, "NR7_EMA20": 0.10, "YUM_YUM": 0.10,
    }
}

# Ngưỡng tối thiểu để setup được coi là compatible với regime
REGIME_COMPAT_MIN = 0.40

# ---------------------------------------------------------------------------
# Singleton instances của Module 1 và Module 2
# Khởi tạo một lần, dùng lại cho mọi lệnh (thread-safe, stateless)
# ---------------------------------------------------------------------------

_SESSION_LAYER = SessionProbabilityLayer()
_CONFLUENCE_SCORER = ConfluenceScorer()


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class DeterministicEvalResult:
    """Kết quả đánh giá Layer 2 — không tốn token AI."""
    score: float                         # 0.0–1.0
    approved: bool                       # True nếu score >= threshold
    rejection_reason: Optional[str]      # Lý do từ chối (nếu có)
    components: Dict[str, float] = field(default_factory=dict)  # Breakdown điểm
    triggered_lesson_rules: List[str] = field(default_factory=list)
    layer: str = "LAYER2_DETERMINISTIC"

    # Sub-scores để debug
    regime_compat_score: float = 0.0
    rr_score: float = 0.0
    sl_score: float = 0.0
    wholesale_score: float = 0.0
    macro_penalty: float = 0.0
    lessons_delta: float = 0.0

    # --- Module 1: Session info ---
    session_name: str = ""               # Tên phiên (VD: "LONDON_OPEN")
    session_multiplier: float = 1.0      # Hệ số phiên nhân vào score cuối

    # --- Module 2: Confluence info ---
    confluence_score: float = 0.0        # Điểm hội tụ Bayesian (0–1)
    confluence_signals_passed: int = 0   # Số tín hiệu pass (0–5)
    confluence_bonus: float = 0.0        # Điểm cộng thêm từ confluence


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class PreEntryScorer:
    """
    Layer 2 Deterministic Scorer.
    Tất cả logic là toán học thuần túy — không gọi AI, không tốn token.
    """

    # Ngưỡng mặc định để escalate lên AI (Layer 3) - Nâng lên 0.80 để tăng độ chọn lọc và kỷ luật
    DEFAULT_THRESHOLD = 0.80

    @staticmethod
    def evaluate(
        setup: str,
        side: str,
        regime: str,
        wholesale: Dict[str, Any],
        macro_bias: str = "NEUTRAL",
        profile: Any = None,           # InstrumentProfile
        lesson_rules: Optional[List[LessonRule]] = None,
        threshold: float = DEFAULT_THRESHOLD,
        # --- Tham số mới: Module 1 (Session) ---
        hour_utc7: int = -1,           # Giờ VN (UTC+7). -1 = bỏ qua kiểm tra phiên
        # --- Tham số mới: Module 2 (Confluence) ---
        confluence_result: Optional[Any] = None,  # ConfluenceResult từ ConfluenceScorer
    ) -> DeterministicEvalResult:
        """
        Tính điểm deterministic cho một candidate trade.

        Args:
            setup:              Tên setup (PB, TST, ...)
            side:               BUY hoặc SELL
            regime:             Market regime hiện tại
            wholesale:          WholesaleCalculation.__dict__
            macro_bias:         Macro news bias (BULLISH/BEARISH/NEUTRAL)
            profile:            InstrumentProfile — cần cho SL floor check
            lesson_rules:       Danh sách LessonRule đã compile sẵn
            threshold:          Ngưỡng để pass Layer 2 (mặc định 0.80)
            hour_utc7:          Giờ Việt Nam (UTC+7) để kiểm tra phiên.
                                Truyền -1 để bỏ qua (backtest mode).
            confluence_result:  ConfluenceResult từ Module 2. Nếu có và
                                min_signals_met=True → cộng bonus vào score.

        Returns:
            DeterministicEvalResult với score và lý do chi tiết
        """
        # ------------------------------------------------------------------
        # HARD FAIL 0: Session Gate (Module 1)
        # Kiểm tra TRƯỚC TẤT CẢ các điều kiện khác để tiết kiệm tài nguyên.
        # DEAD_ZONE hoặc NR7_EMA20 → từ chối ngay lập tức.
        # ------------------------------------------------------------------
        session_name = _SESSION_LAYER.get_current_session(hour_utc7) if hour_utc7 >= 0 else "SKIPPED"
        session_multiplier = _SESSION_LAYER.get_session_multiplier(hour_utc7) if hour_utc7 >= 0 else 1.0

        if hour_utc7 >= 0:
            should_block, block_reason = _SESSION_LAYER.should_block_trade(setup, hour_utc7)
            if should_block:
                return DeterministicEvalResult(
                    score=0.0,
                    approved=False,
                    rejection_reason=f"[SESSION HARD FAIL] {block_reason}",
                    session_name=session_name,
                    session_multiplier=session_multiplier,
                )

        # ------------------------------------------------------------------
        # F0: Wholesale Validity — HARD FAIL (không cần tính thêm)
        # ------------------------------------------------------------------
        if wholesale and wholesale.get("is_valid_entry") is False:
            return DeterministicEvalResult(
                score=0.0,
                approved=False,
                rejection_reason=(
                    f"Điểm vào vượt ngoài vùng Wholesale (LWP={wholesale.get('LWP')}, "
                    f"LRP={wholesale.get('LRP')}). R:R không đảm bảo."
                ),
                wholesale_score=0.0,
                session_name=session_name,
                session_multiplier=session_multiplier,
            )

        # ------------------------------------------------------------------
        # F1: Regime × Setup Compatibility Score
        # ------------------------------------------------------------------
        setup_matrix = REGIME_SETUP_MATRIX.get(regime.upper(), {})
        regime_compat = setup_matrix.get(setup.upper(), 0.30)  # default 0.30 nếu không có trong matrix

        if regime_compat < REGIME_COMPAT_MIN:
            return DeterministicEvalResult(
                score=0.0,
                approved=False,
                rejection_reason=(
                    f"Setup {setup} không phù hợp chế độ {regime} "
                    f"(điểm tương thích {regime_compat:.2f} < {REGIME_COMPAT_MIN:.2f})."
                ),
                regime_compat_score=regime_compat,
                session_name=session_name,
                session_multiplier=session_multiplier,
            )

        # ------------------------------------------------------------------
        # F2: R:R Score
        # rr < min_rr → HARD FAIL
        # rr = min_rr → 0.70
        # rr = 1.5    → 0.85
        # rr >= 2.0   → 1.00
        # ------------------------------------------------------------------
        rr = float(wholesale.get("rr_ratio_part1", 0.0))
        min_rr = float(wholesale.get("min_rr_ratio", 0.75))
        if rr <= 0:
            # Không có R:R info → assume valid nếu wholesale passed
            rr_score = 0.70
        elif rr < min_rr:
            return DeterministicEvalResult(
                score=0.0,
                approved=False,
                rejection_reason=(
                    f"R:R Part 1 ({rr:.2f}) thấp hơn mức tối thiểu {min_rr:.2f} theo cấu hình hệ thống. "
                    f"Không đủ điều kiện vào lệnh."
                ),
                regime_compat_score=regime_compat,
                rr_score=0.0,
                session_name=session_name,
                session_multiplier=session_multiplier,
            )
        else:
            # Linear scale: min_rr→0.70, 2.0→1.00
            if rr >= 2.0:
                rr_score = 1.0
            else:
                rr_score = min(1.0, 0.70 + ((rr - min_rr) / max(2.0 - min_rr, 0.0001)) * 0.30)

        # ------------------------------------------------------------------
        # F3: SL Distance Safety Score
        # Nếu không có profile, skip (assume OK)
        # ------------------------------------------------------------------
        order_price = float(wholesale.get("recommended_entry", 0.0))
        sl_price = float(wholesale.get("S1", 0.0))
        risk_dist = abs(order_price - sl_price) if order_price and sl_price else 0.0

        if profile and risk_dist > 0:
            min_sl_floor = profile.min_sl_points * 0.8
            if risk_dist < min_sl_floor:
                return DeterministicEvalResult(
                    score=0.0,
                    approved=False,
                    rejection_reason=(
                        f"Khoảng cách SL ({risk_dist:.4f}) quá hẹp so với sàn an toàn "
                        f"({min_sl_floor:.4f}). Nguy cơ bị quét do spread."
                    ),
                    regime_compat_score=regime_compat,
                    rr_score=rr_score,
                    sl_score=0.0,
                    session_name=session_name,
                    session_multiplier=session_multiplier,
                )
            sl_score = min(1.0, risk_dist / (profile.min_sl_points * 2.0))
        else:
            sl_score = 0.75  # assume neutral nếu không có profile

        # ------------------------------------------------------------------
        # F4: Wholesale Boundary Score (graded, nếu is_valid_entry=True)
        # Đo khoảng cách từ entry đến LWP/LRP — càng gần giữa vùng càng tốt
        # ------------------------------------------------------------------
        lwp = wholesale.get("LWP")
        lrp = wholesale.get("LRP")
        if lwp and lrp and order_price:
            zone_mid = (float(lwp) + float(lrp)) / 2.0
            zone_half = abs(float(lwp) - float(lrp)) / 2.0
            if zone_half > 0:
                dist_from_mid = abs(order_price - zone_mid)
                wholesale_score = max(0.5, 1.0 - (dist_from_mid / zone_half) * 0.5)
            else:
                wholesale_score = 1.0
        else:
            wholesale_score = 0.80  # assume OK nếu no LWP/LRP data

        # ------------------------------------------------------------------
        # F5: Macro Bias Penalty
        # BULLISH + SELL → -0.50; BEARISH + BUY → -0.50
        # ------------------------------------------------------------------
        macro_upper = macro_bias.upper()
        macro_penalty = 0.0
        if "BULLISH" in macro_upper and side.upper() == "SELL":
            macro_penalty = 0.50
        elif "BEARISH" in macro_upper and side.upper() == "BUY":
            macro_penalty = 0.50

        # ------------------------------------------------------------------
        # F6: Lessons-as-Rules Application
        # ------------------------------------------------------------------
        lessons_delta = 0.0
        triggered_lessons: List[str] = []
        if lesson_rules:
            delta, triggered = LessonsCompiler.apply_rules(lesson_rules, setup, regime, side)
            lessons_delta = delta
            triggered_lessons = triggered
            # REJECT action từ lesson → hard fail
            if lessons_delta <= -1.0:
                return DeterministicEvalResult(
                    score=0.0,
                    approved=False,
                    rejection_reason=f"Bài học kinh nghiệm: {triggered_lessons[0] if triggered_lessons else 'Lesson REJECT rule'}",
                    triggered_lesson_rules=triggered_lessons,
                    lessons_delta=lessons_delta,
                    session_name=session_name,
                    session_multiplier=session_multiplier,
                )

        # ------------------------------------------------------------------
        # FINAL WEIGHTED SCORE (trước khi áp dụng session và confluence)
        # ------------------------------------------------------------------
        raw_score = (
            0.35 * regime_compat +
            0.30 * rr_score +
            0.20 * sl_score +
            0.15 * wholesale_score
        )
        base_final = max(0.0, min(1.0, raw_score - macro_penalty + lessons_delta))

        # ------------------------------------------------------------------
        # BONUS: Confluence Score (Module 2)
        # Nếu confluence_result.min_signals_met → cộng 0.05 × confluence_score
        # Logic: Không phải điều kiện bắt buộc, chỉ là tín hiệu tăng xác suất.
        # Caller nên chạy ConfluenceScorer.evaluate() trước và truyền vào đây.
        # ------------------------------------------------------------------
        confluence_bonus = 0.0
        c_score = 0.0
        c_signals = 0
        if confluence_result is not None:
            c_score = getattr(confluence_result, "confluence_score", 0.0)
            c_signals = getattr(confluence_result, "signals_passed", 0)
            c_met = getattr(confluence_result, "min_signals_met", False)
            if c_met:
                # Cộng tối đa 0.05 điểm (5%) khi confluence đầy đủ
                confluence_bonus = 0.05 * c_score

        # ------------------------------------------------------------------
        # MULTIPLY BY SESSION MULTIPLIER (Module 1)
        # Nhân sau khi đã tổng hợp raw_score + lessons + confluence_bonus.
        # Session multiplier phản ánh chất lượng thanh khoản phiên đó.
        # ------------------------------------------------------------------
        pre_session_score = min(1.0, base_final + confluence_bonus)
        final_score = max(0.0, min(1.0, pre_session_score * session_multiplier))

        approved = final_score >= threshold

        components = {
            "regime_compat":     round(0.35 * regime_compat, 4),
            "rr_quality":        round(0.30 * rr_score, 4),
            "sl_safety":         round(0.20 * sl_score, 4),
            "wholesale_fit":     round(0.15 * wholesale_score, 4),
            "macro_penalty":     round(-macro_penalty, 4),
            "lessons_delta":     round(lessons_delta, 4),
            "confluence_bonus":  round(confluence_bonus, 4),
            "session_multiplier": round(session_multiplier, 4),
            "final":             round(final_score, 4),
        }

        rejection_reason = None
        if not approved:
            # Thêm cảnh báo session nếu có (setup không phù hợp phiên nhưng không bị block cứng)
            session_warning = ""
            if hour_utc7 >= 0:
                _, warn = _SESSION_LAYER.should_block_trade(setup, hour_utc7)
                if warn.startswith("[CẢNH BÁO]"):
                    session_warning = f" | {warn}"

            rejection_reason = (
                f"Score {final_score:.2f} < threshold {threshold:.2f}. "
                f"Breakdown: regime={regime_compat:.2f}, RR={rr_score:.2f}, "
                f"SL={sl_score:.2f}, wholesale={wholesale_score:.2f}, "
                f"macro_penalty={macro_penalty:.2f}, lessons={lessons_delta:.2f}, "
                f"confluence_bonus={confluence_bonus:.3f}, "
                f"session={session_name}(×{session_multiplier:.2f})"
                f"{session_warning}"
            )

        return DeterministicEvalResult(
            score=round(final_score, 4),
            approved=approved,
            rejection_reason=rejection_reason,
            components=components,
            triggered_lesson_rules=triggered_lessons,
            regime_compat_score=regime_compat,
            rr_score=rr_score,
            sl_score=sl_score,
            wholesale_score=wholesale_score,
            macro_penalty=macro_penalty,
            lessons_delta=lessons_delta,
            session_name=session_name,
            session_multiplier=session_multiplier,
            confluence_score=round(c_score, 4),
            confluence_signals_passed=c_signals,
            confluence_bonus=round(confluence_bonus, 4),
        )

    @staticmethod
    def score_summary(result: DeterministicEvalResult) -> str:
        """Format compact string để log. Bao gồm cả session và confluence info."""
        return (
            f"L2[{result.score:.2f}] "
            f"regime={result.regime_compat_score:.2f} "
            f"rr={result.rr_score:.2f} "
            f"sl={result.sl_score:.2f} "
            f"ws={result.wholesale_score:.2f} "
            f"macro=-{result.macro_penalty:.2f} "
            f"lessons={result.lessons_delta:+.2f} "
            f"conf={result.confluence_signals_passed}/5(+{result.confluence_bonus:.3f}) "
            f"sess={result.session_name}(×{result.session_multiplier:.2f}) "
            f"-> {'PASS' if result.approved else 'REJECT'}"
        )
