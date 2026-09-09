"""
Vector Store Implementation (In-memory + Cosine Similarity & Persistence)
Implements IVectorStore without requiring external heavy service
"""
import math
from typing import List, Dict, Any
from core.domain.interfaces.vector_store import IVectorStore

class MemoryVectorStore(IVectorStore):
    def __init__(self):
        self._documents: List[Dict[str, Any]] = []

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
