"""
Module 2: Confluence Probability Score (XAUUSD)
================================================
Tính điểm hội tụ (confluence) từ 5 tín hiệu kỹ thuật độc lập.
Sử dụng Bayesian combination để tránh double-counting khi các tín hiệu tương quan.

Yêu cầu: TỐI THIỂU 3/5 tín hiệu phải pass trước khi vào lệnh.

Bối cảnh dữ liệu thực (130 lệnh XAUUSD, lot 0.01, spread 6.5 USD):
  - WR thực chỉ 6.15% → cần bộ lọc chặt hơn rất nhiều
  - 116/130 lệnh là scratch (thoát huề/lỗ nhỏ) → phần lớn entry đều sai điểm
  - Confluence score giúp tập trung vào những setup có XS cao nhất

5 tín hiệu confluence:
  1. htf_bias_aligned     — HTF (H4/D1) bias khớp hướng lệnh
  2. m3_structure_clear   — M3 structure rõ ràng (BOS xác nhận)
  3. sr_zone_strength     — Vùng SR có ý nghĩa (ít nhất 2 lần test)
  4. price_rejection_quality — Nến rejection quality (wick ratio tốt)
  5. volume_context       — Context macro/volume xác nhận
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Ngưỡng tín hiệu confluence
# ---------------------------------------------------------------------------

# Tối thiểu bao nhiêu tín hiệu phải pass để được vào lệnh
MIN_SIGNALS_REQUIRED = 3

# Tổng số tín hiệu trong hệ thống
TOTAL_SIGNALS = 5

# Ngưỡng wick ratio để coi là "rejection chất lượng"
# (wick dài hơn thân nến ít nhất 1.5 lần)
WICK_RATIO_THRESHOLD = 1.5

# Số lần test vùng SR tối thiểu để coi là "significant"
MIN_ZONE_TESTS = 2


# ---------------------------------------------------------------------------
# Dataclass kết quả confluence
# ---------------------------------------------------------------------------

@dataclass
class ConfluenceResult:
    """
    Kết quả đánh giá confluence của một setup.

    Attributes:
        signals_passed:    Số tín hiệu pass (0–5)
        total_signals:     Tổng số tín hiệu (luôn = 5)
        confluence_score:  Điểm hội tụ Bayesian (0.0–1.0)
        min_signals_met:   True nếu signals_passed >= MIN_SIGNALS_REQUIRED
        details:           Chi tiết từng tín hiệu {tên: (passed, probability)}
    """
    signals_passed: int
    total_signals: int
    confluence_score: float               # 0.0–1.0
    min_signals_met: bool                 # True nếu >= 3/5 tín hiệu pass
    details: Dict[str, Tuple[bool, float]] = field(default_factory=dict)
    # {signal_name: (passed: bool, probability: float)}


# ---------------------------------------------------------------------------
# Confluence Scorer
# ---------------------------------------------------------------------------

class ConfluenceScorer:
    """
    Tính điểm hội tụ từ 5 tín hiệu kỹ thuật độc lập.

    Phương pháp Bayesian Combination:
    ─────────────────────────────────
    Thay vì cộng đơn giản (dễ double-count), ta dùng:
        P(A and B) = P(A) × P(B|A)
    Tuy nhiên, vì tín hiệu không độc lập hoàn toàn, ta dùng:
        bayesian_score = 1 - ∏(1 - p_i)  for all passing signals
    Cách này cho ra score cao hơn khi nhiều tín hiệu cùng xác nhận,
    và không bị cap ở mức trung bình.

    Ví dụ sử dụng:
        scorer = ConfluenceScorer()
        result = scorer.evaluate(
            htf_bias="BULLISH", side="BUY",
            m3_trend="UP", zone_significance="MAJOR",
            wick_ratio=2.1, prior_zone_tests=3,
            macro_bias="BULLISH"
        )
        print(scorer.format_summary(result))
    """

    # ------------------------------------------------------------------
    # Bảng xác suất cơ bản (prior) cho từng tín hiệu
    # Dựa trên tần suất lịch sử và mức độ quan trọng
    # ------------------------------------------------------------------

    # HTF bias aligned — tín hiệu quan trọng nhất
    _PROB_HTF_ALIGNED = 0.75

    # M3 structure rõ ràng (BOS xác nhận hướng)
    _PROB_M3_CLEAR = 0.70

    # SR zone có ý nghĩa (đã test >= 2 lần)
    _PROB_SR_SIGNIFICANT = 0.68

    # Price rejection quality (wick ratio tốt)
    _PROB_REJECTION_QUALITY = 0.65

    # Volume/macro context xác nhận
    _PROB_VOLUME_CONTEXT = 0.60

    # Xác suất khi tín hiệu KHÔNG pass (weak evidence)
    _PROB_NOT_PASSING = 0.30

    # ------------------------------------------------------------------
    # Đánh giá từng tín hiệu riêng lẻ
    # ------------------------------------------------------------------

    @staticmethod
    def _eval_htf_bias(htf_bias: str, side: str) -> Tuple[bool, float]:
        """
        Tín hiệu 1: HTF Bias Aligned
        HTF (H4/D1) bias phải khớp hướng lệnh.

        Args:
            htf_bias: "BULLISH", "BEARISH", "NEUTRAL"
            side:     "BUY" hoặc "SELL"

        Returns:
            (passed: bool, probability: float)
        """
        htf_upper = htf_bias.upper()
        side_upper = side.upper()

        # HTF bias khớp hoàn toàn → xác suất cao nhất
        if htf_upper == "BULLISH" and side_upper == "BUY":
            return True, ConfluenceScorer._PROB_HTF_ALIGNED

        if htf_upper == "BEARISH" and side_upper == "SELL":
            return True, ConfluenceScorer._PROB_HTF_ALIGNED

        # HTF NEUTRAL → trung lập, coi là fail (không có confirmation)
        if htf_upper == "NEUTRAL":
            return False, ConfluenceScorer._PROB_NOT_PASSING

        # HTF bias ngược chiều → xác suất rất thấp, fail
        return False, 0.15

    @staticmethod
    def _eval_m3_structure(m3_trend: str, side: str) -> Tuple[bool, float]:
        """
        Tín hiệu 2: M3 Structure Clear
        M3 structure phải có BOS (Break of Structure) rõ ràng theo hướng lệnh.

        Args:
            m3_trend: "UP", "DOWN", "SIDEWAYS", "CHOPPY"
            side:     "BUY" hoặc "SELL"

        Returns:
            (passed: bool, probability: float)
        """
        m3_upper = m3_trend.upper()
        side_upper = side.upper()

        # Cấu trúc BUY khớp với hướng
        if m3_upper == "UP" and side_upper == "BUY":
            return True, ConfluenceScorer._PROB_M3_CLEAR

        # Cấu trúc SELL khớp với hướng
        if m3_upper == "DOWN" and side_upper == "SELL":
            return True, ConfluenceScorer._PROB_M3_CLEAR

        # Sideways — structure không rõ, fail
        if m3_upper in ("SIDEWAYS", "CHOPPY"):
            return False, 0.20

        # Ngược chiều — fail rõ ràng
        return False, 0.15

    @staticmethod
    def _eval_sr_zone(
        zone_significance: str, prior_zone_tests: int
    ) -> Tuple[bool, float]:
        """
        Tín hiệu 3: SR Zone Strength
        Vùng SR phải có ý nghĩa và đã được test nhiều lần.

        Args:
            zone_significance: "MAJOR", "MINOR", "WEAK", "NONE"
            prior_zone_tests:  Số lần giá đã test vùng này trước đó

        Returns:
            (passed: bool, probability: float)
        """
        sig_upper = zone_significance.upper()

        # MAJOR zone với đủ số lần test → pass mạnh nhất
        if sig_upper == "MAJOR" and prior_zone_tests >= MIN_ZONE_TESTS:
            return True, ConfluenceScorer._PROB_SR_SIGNIFICANT

        # MAJOR zone nhưng chưa đủ test → pass yếu hơn
        if sig_upper == "MAJOR" and prior_zone_tests == 1:
            return True, ConfluenceScorer._PROB_SR_SIGNIFICANT * 0.85

        # MINOR zone với đủ test → pass mức trung bình
        if sig_upper == "MINOR" and prior_zone_tests >= MIN_ZONE_TESTS:
            return True, ConfluenceScorer._PROB_SR_SIGNIFICANT * 0.75

        # MINOR zone mới (1 lần test) → fail
        if sig_upper == "MINOR" and prior_zone_tests < MIN_ZONE_TESTS:
            return False, ConfluenceScorer._PROB_NOT_PASSING

        # WEAK hoặc NONE → fail
        return False, 0.20

    @staticmethod
    def _eval_price_rejection(wick_ratio: float) -> Tuple[bool, float]:
        """
        Tín hiệu 4: Price Rejection Quality
        Wick ratio = wick_length / body_length.
        Wick dài → giá đã bị rejection mạnh → tín hiệu reversal tốt.

        Thang điểm:
          wick_ratio >= 3.0 → xác suất 0.75 (rejection rất mạnh)
          wick_ratio >= 2.0 → xác suất 0.70
          wick_ratio >= 1.5 → xác suất 0.65 (ngưỡng tối thiểu để pass)
          wick_ratio <  1.5 → fail (thân nến quá lớn, không có rejection rõ)

        Args:
            wick_ratio: Tỷ lệ wick/body (>= 0)

        Returns:
            (passed: bool, probability: float)
        """
        if wick_ratio >= 3.0:
            # Rejection rất mạnh — pin bar dài
            return True, 0.75

        if wick_ratio >= 2.0:
            # Rejection tốt
            return True, ConfluenceScorer._PROB_REJECTION_QUALITY + 0.05

        if wick_ratio >= WICK_RATIO_THRESHOLD:  # >= 1.5
            # Rejection đủ điều kiện tối thiểu
            return True, ConfluenceScorer._PROB_REJECTION_QUALITY

        # Wick quá ngắn so với thân → không có rejection rõ ràng
        return False, 0.25

    @staticmethod
    def _eval_volume_context(macro_bias: str, side: str) -> Tuple[bool, float]:
        """
        Tín hiệu 5: Volume/Macro Context
        Macro bias xác nhận hướng lệnh (news, sentiment, institutional flow).

        Trong bối cảnh XAUUSD:
          - BULLISH macro → FED dovish, risk-off, geopolitical risk
          - BEARISH macro → USD mạnh, risk-on, FED hawkish

        Args:
            macro_bias: "BULLISH", "BEARISH", "NEUTRAL"
            side:       "BUY" hoặc "SELL"

        Returns:
            (passed: bool, probability: float)
        """
        macro_upper = macro_bias.upper()
        side_upper = side.upper()

        # Macro xác nhận hướng BUY
        if macro_upper == "BULLISH" and side_upper == "BUY":
            return True, ConfluenceScorer._PROB_VOLUME_CONTEXT

        # Macro xác nhận hướng SELL
        if macro_upper == "BEARISH" and side_upper == "SELL":
            return True, ConfluenceScorer._PROB_VOLUME_CONTEXT

        # NEUTRAL — không có thông tin macro rõ ràng
        # Coi là pass nhẹ (0.50) để không penalty quá nhiều khi không có news
        if macro_upper == "NEUTRAL":
            return True, 0.50

        # Macro ngược chiều — fail
        return False, 0.20

    # ------------------------------------------------------------------
    # Bayesian Combination
    # ------------------------------------------------------------------

    @staticmethod
    def _bayesian_combine(probabilities: list[float]) -> float:
        """
        Kết hợp Bayesian các xác suất độc lập.

        Công thức: P(at_least_one) = 1 - ∏(1 - p_i)
        Cách này đánh giá "xác suất ít nhất 1 trong n tín hiệu đúng",
        phù hợp hơn là trung bình cộng (dễ bị diluted bởi tín hiệu yếu).

        Ví dụ:
          [0.75, 0.70, 0.68] → 1 - (0.25 × 0.30 × 0.32) = 1 - 0.024 = 0.976
          Nhưng đây là quá cao, nên ta normalize về 0–1 dựa trên số tín hiệu.

        Thực tế ta dùng geometric mean của các passing probabilities
        để tránh inflated result:
          score = (∏ p_i)^(1/n)

        Args:
            probabilities: List xác suất của các tín hiệu PASS (không empty)

        Returns:
            float score 0.0–1.0
        """
        if not probabilities:
            return 0.0

        # Geometric mean — robust hơn arithmetic mean với xác suất
        product = 1.0
        for p in probabilities:
            product *= max(0.01, min(0.99, p))  # Clamp để tránh log(0)

        geometric_mean = product ** (1.0 / len(probabilities))

        # Scale thêm theo số tín hiệu pass / tổng tín hiệu
        # → Nhiều tín hiệu pass hơn → score cao hơn
        coverage_bonus = len(probabilities) / TOTAL_SIGNALS
        combined = geometric_mean * 0.7 + coverage_bonus * 0.3

        return min(1.0, max(0.0, combined))

    # ------------------------------------------------------------------
    # Public API: evaluate()
    # ------------------------------------------------------------------

    def evaluate(
        self,
        htf_bias: str,
        side: str,
        m3_trend: str,
        zone_significance: str,
        wick_ratio: float,
        prior_zone_tests: int,
        macro_bias: str = "NEUTRAL",
    ) -> ConfluenceResult:
        """
        Đánh giá toàn bộ 5 tín hiệu confluence và tính điểm hội tụ.

        Args:
            htf_bias:          H4/D1 bias: "BULLISH", "BEARISH", "NEUTRAL"
            side:              Hướng lệnh: "BUY" hoặc "SELL"
            m3_trend:          M3 market structure: "UP", "DOWN", "SIDEWAYS", "CHOPPY"
            zone_significance: Ý nghĩa vùng SR: "MAJOR", "MINOR", "WEAK", "NONE"
            wick_ratio:        Tỷ lệ wick/body của nến entry (>= 0.0)
            prior_zone_tests:  Số lần giá đã test vùng SR này trước đó (>= 0)
            macro_bias:        Macro/news bias: "BULLISH", "BEARISH", "NEUTRAL"

        Returns:
            ConfluenceResult với đầy đủ thông tin để quyết định entry

        Ví dụ sử dụng:
            result = scorer.evaluate(
                htf_bias="BULLISH", side="BUY",
                m3_trend="UP", zone_significance="MAJOR",
                wick_ratio=2.0, prior_zone_tests=3,
                macro_bias="NEUTRAL"
            )
            if result.min_signals_met:
                # Được phép tiếp tục đến Layer 2
                pass
        """
        # --- Đánh giá từng tín hiệu ---
        htf_passed, htf_prob = self._eval_htf_bias(htf_bias, side)
        m3_passed, m3_prob = self._eval_m3_structure(m3_trend, side)
        sr_passed, sr_prob = self._eval_sr_zone(zone_significance, prior_zone_tests)
        rej_passed, rej_prob = self._eval_price_rejection(wick_ratio)
        vol_passed, vol_prob = self._eval_volume_context(macro_bias, side)

        # --- Tổng hợp kết quả ---
        details: Dict[str, Tuple[bool, float]] = {
            "htf_bias_aligned":         (htf_passed, round(htf_prob, 4)),
            "m3_structure_clear":       (m3_passed, round(m3_prob, 4)),
            "sr_zone_strength":         (sr_passed, round(sr_prob, 4)),
            "price_rejection_quality":  (rej_passed, round(rej_prob, 4)),
            "volume_context":           (vol_passed, round(vol_prob, 4)),
        }

        # Danh sách xác suất của các tín hiệu PASS
        passing_probs = [
            prob
            for (passed, prob) in details.values()
            if passed
        ]

        signals_passed = len(passing_probs)
        min_signals_met = signals_passed >= MIN_SIGNALS_REQUIRED

        # Nếu không đủ tín hiệu → score = 0.0 (không Bayesian combine)
        if not min_signals_met:
            confluence_score = 0.0
        else:
            confluence_score = self._bayesian_combine(passing_probs)

        return ConfluenceResult(
            signals_passed=signals_passed,
            total_signals=TOTAL_SIGNALS,
            confluence_score=round(confluence_score, 4),
            min_signals_met=min_signals_met,
            details=details,
        )

    # ------------------------------------------------------------------
    # Public API: format_summary()
    # ------------------------------------------------------------------

    @staticmethod
    def format_summary(result: ConfluenceResult) -> str:
        """
        Format kết quả confluence thành chuỗi dễ đọc để log/journal.

        Ví dụ output:
            CONFLUENCE [3/5 ✓] score=0.71
              ✓ htf_bias_aligned        p=0.7500
              ✓ m3_structure_clear      p=0.7000
              ✗ sr_zone_strength        p=0.3000
              ✓ price_rejection_quality p=0.6500
              ✗ volume_context          p=0.5000

        Args:
            result: ConfluenceResult từ evaluate()

        Returns:
            str formatted summary
        """
        status = "✓ MIN MET" if result.min_signals_met else "✗ MIN FAIL"
        lines = [
            f"CONFLUENCE [{result.signals_passed}/{result.total_signals} {status}] "
            f"score={result.confluence_score:.4f}"
        ]

        for signal_name, (passed, prob) in result.details.items():
            icon = "✓" if passed else "✗"
            lines.append(f"  {icon} {signal_name:<30} p={prob:.4f}")

        if not result.min_signals_met:
            lines.append(
                f"  → BLOCKED: Chỉ {result.signals_passed}/{result.total_signals} tín hiệu "
                f"pass, cần tối thiểu {MIN_SIGNALS_REQUIRED}."
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tiện ích: Lấy danh sách tín hiệu fail để hiển thị cho trader
    # ------------------------------------------------------------------

    @staticmethod
    def get_failing_signals(result: ConfluenceResult) -> list[str]:
        """
        Trả về danh sách tên các tín hiệu KHÔNG pass.
        Hữu ích để trader biết cần cải thiện điểm nào.

        Args:
            result: ConfluenceResult từ evaluate()

        Returns:
            List[str] tên tín hiệu fail
        """
        return [
            name
            for name, (passed, _) in result.details.items()
            if not passed
        ]
