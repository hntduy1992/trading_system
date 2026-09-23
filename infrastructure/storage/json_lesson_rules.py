"""
JSON Lesson Rules Persistence
Lưu trữ và nạp LessonRule objects dưới dạng JSON file.
Cho phép rules được persist qua các phiên và load ngay khi khởi động — không cần compile lại.
"""
from __future__ import annotations
import json
import os
from typing import List, Dict, Any
from core.domain.rules.lessons_compiler import LessonRule, LessonsCompiler


# Đường dẫn mặc định — đặt trong data folder của trading_system
DEFAULT_RULES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "lesson_rules.json"
)


class JsonLessonRulesStore:
    """
    Persistent store cho LessonRule objects.
    Cung cấp load/save/append để rules tích lũy qua nhiều phiên giao dịch.
    """

    def __init__(self, filepath: str = DEFAULT_RULES_PATH):
        self.filepath = os.path.abspath(filepath)
        # Đảm bảo thư mục tồn tại
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)

    def load(self) -> List[LessonRule]:
        """Nạp tất cả rules từ JSON file. Trả về list rỗng nếu chưa có file."""
        if not os.path.exists(self.filepath):
            return []
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data: List[Dict[str, Any]] = json.load(f)
            rules = [LessonRule.from_dict(d) for d in data if isinstance(d, dict)]
            # Sort: priority cao (REJECT) trước
            rules.sort(key=lambda r: r.priority, reverse=True)
            return rules
        except (json.JSONDecodeError, KeyError, Exception) as e:
            print(f"[JsonLessonRulesStore] Load failed: {e}. Starting with empty rules.")
            return []

    def save(self, rules: List[LessonRule]) -> None:
        """Lưu toàn bộ rules xuống file JSON."""
        data = [r.to_dict() for r in rules]
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def append_from_lessons(self, lessons: List[str]) -> List[LessonRule]:
        """
        Compile thêm lessons mới vào store hiện có.
        Tự động deduplicate theo source_lesson text.
        Returns: danh sách rules hiện tại (sau khi thêm mới).
        """
        existing = self.load()
        existing_texts = {r.source_lesson.strip() for r in existing}

        new_rules = LessonsCompiler.compile(lessons)
        added = [r for r in new_rules if r.source_lesson.strip() not in existing_texts]

        if added:
            merged = existing + added
            merged.sort(key=lambda r: r.priority, reverse=True)
            self.save(merged)
            print(f"[JsonLessonRulesStore] +{len(added)} new rules added. Total: {len(merged)}")
            return merged
        return existing

    def append_from_structured(self, structured_rules: List[Dict[str, Any]]) -> List[LessonRule]:
        """
        Compile thêm structured_rules (từ AI audit JSON) vào store.
        Structured rules có độ chính xác cao hơn text lessons.
        """
        existing = self.load()
        existing_texts = {r.source_lesson.strip() for r in existing}

        new_rules = LessonsCompiler.compile_from_structured(structured_rules)
        added = [r for r in new_rules if r.source_lesson.strip() not in existing_texts]

        if added:
            merged = existing + added
            merged.sort(key=lambda r: r.priority, reverse=True)
            self.save(merged)
            print(f"[JsonLessonRulesStore] +{len(added)} structured rules added. Total: {len(merged)}")
            return merged
        return existing

    def clear(self) -> None:
        """Xóa toàn bộ rules (reset)."""
        self.save([])
        print(f"[JsonLessonRulesStore] Rules store cleared.")

    def summary(self) -> str:
        """Tóm tắt nhanh số lượng rules theo action."""
        rules = self.load()
        reject_count = sum(1 for r in rules if r.action == "REJECT")
        penalize_count = sum(1 for r in rules if r.action == "PENALIZE")
        boost_count = sum(1 for r in rules if r.action == "BOOST")
        return (
            f"LessonRules: {len(rules)} total "
            f"(REJECT={reject_count}, PENALIZE={penalize_count}, BOOST={boost_count}) "
            f"→ {self.filepath}"
        )
