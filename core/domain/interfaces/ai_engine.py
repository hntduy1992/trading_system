"""
AI Engine Interface (Port) - Abstract base for LLM reasoning (Gemini, Claude, OpenAI, Mock)
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List
from core.domain.models import SessionConfig, AuditReport, PreEntryEvaluation

class IAIEngine(ABC):
    @abstractmethod
    async def generate_pre_session_plan(
        self,
        symbol: str,
        quant_baseline_metrics: Dict[str, Any],
        recent_rates_json: Dict[str, Any],
        economic_events: List[Dict[str, Any]],
        retrieved_rag_lessons: List[str]
    ) -> SessionConfig:
        """Execute Pre-Session AI Planning Prompt and return validated SessionConfig."""
        pass

    @abstractmethod
    async def evaluate_candidate_trade(
        self,
        candidate_context: Dict[str, Any],
        trading_config: Dict[str, Any],
        recent_bars: Dict[str, Any]
    ) -> PreEntryEvaluation:
        """Evaluate a candidate entry setup before dispatching order to broker."""
        pass

    @abstractmethod
    async def audit_post_session(
        self,
        trading_config_used: Dict[str, Any],
        session_trades_json: List[Dict[str, Any]],
        full_session_ohlcv: Dict[str, Any]
    ) -> AuditReport:
        """Execute Post-Session AI Hindsight Audit answering Lance Beggs 4 questions."""
        pass
