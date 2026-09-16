"""
Vector Store Implementation (In-memory + Cosine Similarity & Persistence)
Implements IVectorStore without requiring external heavy service
"""
import math
from typing import List, Dict, Any, Optional
from core.domain.interfaces.vector_store import IVectorStore

class MemoryVectorStore(IVectorStore):
    def __init__(self):
        self._documents: List[Dict[str, Any]] = []
        self._lesson_rules_raw: List[Dict[str, Any]] = []  # serialized LessonRule dicts

    def _simple_vectorize(self, text: str) -> Dict[str, float]:
        words = text.lower().replace(",", " ").replace(".", " ").split()
        tf: Dict[str, float] = {}
        for w in words:
            tf[w] = tf.get(w, 0.0) + 1.0
        norm = math.sqrt(sum(v * v for v in tf.values())) or 1.0
        return {w: v / norm for w, v in tf.items()}

    def _cosine_sim(self, v1: Dict[str, float], v2: Dict[str, float]) -> float:
        dot = sum(v1[w] * v2.get(w, 0.0) for w in v1)
        return dot

    async def add_lesson(self, lesson_text: str, metadata: Dict[str, Any]) -> None:
        vec = self._simple_vectorize(lesson_text)
        self._documents.append({
            "text": lesson_text,
            "vector": vec,
            "metadata": metadata
        })

    async def search_lessons(self, query: str, regime: str, limit: int = 5) -> List[str]:
        if not self._documents:
            return [
                "Always wait for confirmation of stall before wholesale entry.",
                "Ensure R:R >= 1.0 boundary is strictly respected for Part 1.",
                "Never chase market beyond LWP when trapped traders fail."
            ]

        q_vec = self._simple_vectorize(query)
        scored = []
        for doc in self._documents:
            score = self._cosine_sim(q_vec, doc["vector"])
            # Bonus if matching regime
            if doc["metadata"].get("regime") == regime:
                score += 0.2
            scored.append((score, doc["text"]))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    async def add_lesson_rule(self, rule_dict: Dict[str, Any]) -> None:
        """Lưu một LessonRule (dạng dict) vào memory store."""
        source = rule_dict.get("source_lesson", "").strip()
        # Deduplication theo source_lesson
        existing = {r.get("source_lesson", "").strip() for r in self._lesson_rules_raw}
        if source and source not in existing:
            self._lesson_rules_raw.append(rule_dict)

    async def add_lesson_rules_batch(self, rule_dicts: List[Dict[str, Any]]) -> None:
        """Batch insert nhiều LessonRule dicts cùng lúc."""
        for d in rule_dicts:
            await self.add_lesson_rule(d)

    async def get_lesson_rules(
        self,
        setup: Optional[str] = None,
        regime: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Lấy lesson rules, optionally filter theo setup/regime.
        Trả về list dicts để caller tự convert sang LessonRule objects.
        """
        result = []
        for r in self._lesson_rules_raw:
            if setup and r.get("condition_setup") and r["condition_setup"].upper() != setup.upper():
                continue
            if regime and r.get("condition_regime") and r["condition_regime"].upper() != regime.upper():
                continue
            result.append(r)
        return result

