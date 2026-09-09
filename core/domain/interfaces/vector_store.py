"""
Vector Store Interface (Port) - Abstract base for RAG knowledge base
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any

class IVectorStore(ABC):
    @abstractmethod
    async def add_lesson(self, lesson_text: str, metadata: Dict[str, Any]) -> None:
        """Store a hindsight audited lesson with metadata (regime, symbol, expectancy, win_rate)."""
        pass

    @abstractmethod
    async def search_lessons(self, query: str, regime: str, limit: int = 5) -> List[str]:
        """Retrieve relevant past lessons matching current regime and context."""
        pass
