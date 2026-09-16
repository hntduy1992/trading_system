"""
Lessons-as-Rules Compiler
Biên dịch lessons_learned (text tự nhiên) → LessonRule objects (machine-parseable).
Giúp máy chủ áp dụng bài học mà KHÔNG cần gọi AI — tiết kiệm 100% token trên mỗi rule match.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import re

# ---------------------------------------------------------------------------
# LessonRule dataclass
# ---------------------------------------------------------------------------

@dataclass
class LessonRule:
    """
    Một quy tắc được trích xuất từ bài học kinh nghiệm.
    Áp dụng trong Layer 2 Deterministic Scorer mà không cần AI.
    """
    condition_setup: Optional[str] = None       # e.g. "PB", "TST", None = any setup
    condition_regime: Optional[str] = None      # e.g. "SIDEWAYS_RANGE", None = any
    condition_side: Optional[str] = None        # e.g. "BUY", "SELL", None = any
    action: str = "PENALIZE"                    # "REJECT" | "PENALIZE" | "BOOST"
    confidence_delta: float = -0.10             # penalty (<0) hoặc boost (>0)
    source_lesson: str = ""                     # Original lesson text
    priority: int = 0                           # Higher = applied first

    def matches(self, setup: str, regime: str, side: str) -> bool:
        """Kiểm tra xem rule có áp dụng cho context hiện tại không."""
        if self.condition_setup and self.condition_setup.upper() != setup.upper():
            return False
        if self.condition_regime and self.condition_regime.upper() != regime.upper():
            return False
        if self.condition_side and self.condition_side.upper() != side.upper():
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "condition_setup": self.condition_setup,
            "condition_regime": self.condition_regime,
            "condition_side": self.condition_side,
            "action": self.action,
            "confidence_delta": self.confidence_delta,
            "source_lesson": self.source_lesson,
            "priority": self.priority
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LessonRule":
        return LessonRule(
            condition_setup=d.get("condition_setup"),
            condition_regime=d.get("condition_regime"),
            condition_side=d.get("condition_side"),
            action=d.get("action", "PENALIZE"),
            confidence_delta=float(d.get("confidence_delta", -0.10)),
            source_lesson=d.get("source_lesson", ""),
            priority=int(d.get("priority", 0))
        )


# ---------------------------------------------------------------------------
# Keyword maps
# ---------------------------------------------------------------------------

_REJECT_PATTERNS = [
    r"tuyệt đối không",
    r"tuyet doi khong",
    r"cấm",
    r"cam ",
    r"không mở lại",
    r"khong mo lai",
    r"không được",
    r"khong duoc",
    r"không vào lệnh",
    r"khong vao lenh",
    r"không dùng lệnh market",
    r"khong dung lenh market",
    r"không dùng",
    r"khong dung",
    r"bị vô hiệu hóa",
    r"bi vo hieu hoa",
    r"không áp dụng",
    r"không thực hiện",
    r"never ",
    r"absolutely not",
    r"strictly prohibited",
    r"do not ",
    r"must not ",
]

_PENALIZE_PATTERNS = [
    r"cẩn thận",
    r"can than",
    r"thận trọng",
    r"than trong",
    r"kiên nhẫn đợi",
    r"kien nhan doi",
    r"chú ý",
    r"chu y",
    r"hạn chế",
    r"han che",
    r"tránh",
    r"tranh ",
    r"giảm lot",
    r"giam lot",
    r"giảm size",
    r"giam size",
    r"giảm rủi ro",
    r"giam rui ro",
    r"dễ bị bẫy",
    r"de bi bay",
    r"dễ bị quét",
    r"de bi quet",
    r"rủi ro cao",
    r"rui ro cao",
    r"không chắc chắn",
    r"khong chac chan",
    r"caution",
    r"be careful",
    r"wait for",
    r"avoid ",
    r"reduce ",
    r"high risk",
]

_BOOST_PATTERNS = [
    r"ưu tiên",
    r"uu tien",
    r"tốt nhất",
    r"tot nhat",
    r"hiệu quả cao",
    r"hieu qua cao",
    r"nên dùng",
    r"nen dung",
    r"phù hợp nhất",
    r"phu hop nhat",
    r"tỷ lệ thắng cao",
    r"ty le thang cao",
    r"rõ ràng nhất",
    r"ro rang nhat",
    r"prefer",
    r"best for",
    r"high win rate",
    r"recommended",
    r"optimal",
]

_REGIME_MAP: Dict[str, List[str]] = {
    "TRENDING_STEADY":    [r"xu hướng rõ", r"xu huong ro", r"trending steady", r"trending_steady", r"xu hướng mạnh", r"xu huong manh"],
    "TRENDING_WEAKENING": [r"xu hướng yếu", r"xu huong yeu", r"trending_weakening", r"trending weakening", r"xu hướng suy yếu", r"xu huong suy yeu"],
    "SIDEWAYS_RANGE":     [r"đi ngang", r"di ngang", r"tích lũy", r"tich luy", r"sideways", r"sideways_range", r"range", r"giằng co", r"giang co"],
    "BREAKOUT_EXPANSION": [r"phá vỡ", r"pha vo", r"breakout", r"breakout_expansion", r"bùng phát", r"bung phat"],
    "CHOPPY_NO_TRADE":    [r"chop", r"nhiễu loạn", r"nhieu loan", r"choppy", r"không rõ xu hướng", r"khong ro xu huong"],
}

_SETUP_MAP: Dict[str, List[str]] = {
    "PB":               [r"\bpb\b", r"pullback"],
    "CPB":              [r"\bcpb\b", r"complex pullback", r"cpb"],
    "TST":              [r"\btst\b", r"test of support", r"test of resistance"],
    "BOF":              [r"\bbof\b", r"breakout fail", r"breakout failure"],
    "BPB":              [r"\bbpb\b", r"breakout pullback"],
    "TREND_BAR_FAIL":   [r"trend bar fail", r"trend_bar_fail"],
    "INSIDE_BAR_SMA21": [r"inside bar", r"inside_bar"],
    "ID_NR4":           [r"\bid.?nr4\b", r"id_nr4"],
    "NR7_EMA20":        [r"\bnr7\b", r"nr7_ema20"],
    "YUM_YUM":          [r"yum.?yum", r"yum_yum"],
}

_SIDE_MAP: Dict[str, List[str]] = {
    "BUY":  [r"\bmua\b", r"\bbuy\b", r"long"],
    "SELL": [r"\bbán\b", r"\bban\b", r"\bsell\b", r"short"],
}


def _match_any(text_lower: str, patterns: List[str]) -> bool:
    return any(re.search(p, text_lower) for p in patterns)


def _detect_regime(text_lower: str) -> Optional[str]:
    for regime, patterns in _REGIME_MAP.items():
        if _match_any(text_lower, patterns):
            return regime
    return None


def _detect_setup(text_lower: str) -> Optional[str]:
    for setup, patterns in _SETUP_MAP.items():
        if _match_any(text_lower, patterns):
            return setup
    return None


def _detect_side(text_lower: str) -> Optional[str]:
    for side, patterns in _SIDE_MAP.items():
        if _match_any(text_lower, patterns):
            return side
    return None


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------

class LessonsCompiler:
    """
    Biên dịch danh sách bài học text → List[LessonRule] để máy chủ áp dụng
    mà không cần gọi AI, tiết kiệm 100% token cho mỗi lần đánh giá.
    """

    @staticmethod
    def compile(lessons: List[str]) -> List[LessonRule]:
        """
        Chuyển danh sách bài học text sang LessonRule objects.
        Các rules được sắp xếp: REJECT trước, PENALIZE sau, BOOST cuối.
        """
        rules: List[LessonRule] = []

        for lesson in lessons:
            if not lesson or not lesson.strip():
                continue

            text_lower = lesson.lower().strip()

            # Xác định action
            if _match_any(text_lower, _REJECT_PATTERNS):
                action = "REJECT"
                delta = -1.0
                priority = 10
            elif _match_any(text_lower, _BOOST_PATTERNS):
                action = "BOOST"
                delta = +0.12
                priority = 1
            elif _match_any(text_lower, _PENALIZE_PATTERNS):
                action = "PENALIZE"
                delta = -0.15
                priority = 5
            else:
                # Không khớp action pattern → bỏ qua (lesson chỉ mang tính mô tả)
                continue

            regime = _detect_regime(text_lower)
            setup = _detect_setup(text_lower)
            side = _detect_side(text_lower)

            rule = LessonRule(
                condition_setup=setup,
                condition_regime=regime,
                condition_side=side,
                action=action,
                confidence_delta=delta,
                source_lesson=lesson.strip(),
                priority=priority
            )
            rules.append(rule)

        # Sort: priority cao (REJECT=10) trước
        rules.sort(key=lambda r: r.priority, reverse=True)
        return rules

    @staticmethod
    def compile_from_structured(structured_rules: List[Dict[str, Any]]) -> List[LessonRule]:
        """
        Compile từ structured_rules JSON (do AI audit trả về) — độ chính xác cao hơn.
        Format: {"condition": {"setup": "PB", "regime": "..."}, "action": "REJECT", "delta": -0.25, ...}

        Safety: REJECT rules với ALL conditions = None sẽ bị downgrade thành PENALIZE(-0.50)
        để tránh blanket-reject toàn bộ trades. Cần ít nhất 1 condition cụ thể để apply REJECT.
        """
        rules: List[LessonRule] = []
        for item in structured_rules:
            cond = item.get("condition") or {}
            action = item.get("action", "PENALIZE").upper()
            priority_map = {"REJECT": 10, "PENALIZE": 5, "BOOST": 1}

            # Extract conditions
            cond_setup = cond.get("setup") if cond else None
            cond_regime = cond.get("regime") if cond else None
            cond_side = cond.get("side") if cond else None

            # Safety: blanket REJECT (all conditions None) → downgrade to strong PENALIZE
            all_conditions_none = (cond_setup is None and cond_regime is None and cond_side is None)
            if action == "REJECT" and all_conditions_none:
                action = "PENALIZE"
                delta = -0.50  # Strong penalty but not a hard block
                priority = 5
            elif action == "REJECT":
                delta = -1.0
                priority = 10
            elif action == "BOOST":
                delta = float(item.get("delta", 0.12))
                priority = 1
            else:  # PENALIZE
                delta = float(item.get("delta", -0.15))
                priority = 5

            rule = LessonRule(
                condition_setup=cond_setup,
                condition_regime=cond_regime,
                condition_side=cond_side,
                action=action,
                confidence_delta=delta,
                source_lesson=item.get("reason", ""),
                priority=priority
            )
            rules.append(rule)

        rules.sort(key=lambda r: r.priority, reverse=True)
        return rules


    @staticmethod
    def apply_rules(
        rules: List[LessonRule],
        setup: str,
        regime: str,
        side: str
    ) -> tuple[float, List[str]]:
        """
        Áp dụng tất cả rules phù hợp.
        Returns: (total_delta, list of triggered rule messages)
        """
        total_delta = 0.0
        triggered: List[str] = []

        for rule in rules:
            if rule.matches(setup, regime, side):
                total_delta += rule.confidence_delta
                triggered.append(
                    f"[{rule.action}] {rule.source_lesson[:80]}... (Δ{rule.confidence_delta:+.2f})"
                    if len(rule.source_lesson) > 80
                    else f"[{rule.action}] {rule.source_lesson} (Δ{rule.confidence_delta:+.2f})"
                )

        return total_delta, triggered
