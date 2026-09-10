"""
Pre-Session Planning Use Case (Server B AI Strategy Engine)
Section 3.1 of YTC Specification
"""
from typing import Dict, Any, List, Optional
from core.domain.models import SessionConfig
from core.domain.interfaces.ai_engine import IAIEngine
from core.domain.interfaces.vector_store import IVectorStore
from core.domain.interfaces.broker import IBrokerGateway

class PreSessionPlannerUseCase:
    def __init__(self, ai_engine: IAIEngine, vector_store: IVectorStore, broker: IBrokerGateway):
        self.ai_engine = ai_engine
        self.vector_store = vector_store
        self.broker = broker

    async def execute(
        self,
        symbol: str,
        economic_events: List[Dict[str, Any]],
        session_tag: Optional[str] = None
    ) -> SessionConfig:
        """
        Gathers baseline metrics, rates, RAG lessons, calls AI to generate trading_config.json
        """
        # Fetch last 24h rates for M30 and M3
        m30_bars = await self.broker.get_latest_bars(symbol, "M30", count=48)
        m3_bars = await self.broker.get_latest_bars(symbol, "M3", count=100)

        rates_json = {
            "M30": [{"time": b.timestamp, "o": b.open, "h": b.high, "l": b.low, "c": b.close} for b in m30_bars[-20:]],
            "M3": [{"time": b.timestamp, "o": b.open, "h": b.high, "l": b.low, "c": b.close} for b in m3_bars[-30:]]
        }

        # Retrieve relevant RAG lessons from memory
        rag_lessons = await self.vector_store.search_lessons(
            query=f"Price action lessons for {symbol} session planning",
            regime="GENERAL",
            limit=5
        )

        quant_metrics = {
            "historical_win_rate": 62.5,
            "avg_rr": 1.45,
            "rolling_20_expectancy": 0.85
        }

        config = await self.ai_engine.generate_pre_session_plan(
            symbol=symbol,
            quant_baseline_metrics=quant_metrics,
            recent_rates_json=rates_json,
            economic_events=economic_events,
            retrieved_rag_lessons=rag_lessons
        )

        from core.domain.rules.session_manager import SessionManager
        status = SessionManager().get_session_status()
        config.session_tag = session_tag or status.session_tag

        return config
