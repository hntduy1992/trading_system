"""
Post-Session Hindsight Auditor Use Case (Server B)
Section 3.2 of YTC Specification
"""
from typing import Dict, Any, List
from core.domain.models import AuditReport
from core.domain.interfaces.ai_engine import IAIEngine
from core.domain.interfaces.vector_store import IVectorStore

class HindsightAuditorUseCase:
    def __init__(self, ai_engine: IAIEngine, vector_store: IVectorStore):
        self.ai_engine = ai_engine
        self.vector_store = vector_store

    async def execute(
        self,
        trading_config_used: Dict[str, Any],
        session_trades_json: List[Dict[str, Any]],
        full_session_ohlcv: Dict[str, Any]
    ) -> AuditReport:
        """
        Executes Lance Beggs 4-question audit, calculates compliance score and records lessons.
        """
        report = await self.ai_engine.audit_post_session(
            trading_config_used=trading_config_used,
            session_trades_json=session_trades_json,
            full_session_ohlcv=full_session_ohlcv
        )

        # Ingest lessons into vector store
        regime = trading_config_used.get("market_regime", "GENERAL")
        symbol = trading_config_used.get("symbol", "EURUSD")
        for lesson in report.lessons_learned:
            await self.vector_store.add_lesson(
                lesson_text=lesson,
                metadata={
                    "regime": regime,
                    "symbol": symbol,
                    "compliance_score": report.compliance_score
                }
            )

        return report
