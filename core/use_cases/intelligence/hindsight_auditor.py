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
        After audit: auto-persists structured_rules to JSON store for Layer 2 scorer next session.
        """
        report = await self.ai_engine.audit_post_session(
            trading_config_used=trading_config_used,
            session_trades_json=session_trades_json,
            full_session_ohlcv=full_session_ohlcv
        )

        # Ingest lessons into vector store (text RAG for pre-session planning)
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

        # ── NEW: Persist structured_rules to JSON store for Layer 2 scorer ──
        # structured_rules có độ chính xác cao hơn text lessons (machine-parseable)
        if report.structured_rules:
            try:
                from infrastructure.storage.json_lesson_rules import JsonLessonRulesStore
                from core.domain.rules.lessons_compiler import LessonsCompiler
                rules_store = JsonLessonRulesStore()
                updated_rules = rules_store.append_from_structured(report.structured_rules)
                print(
                    f"[AUDITOR] {len(report.structured_rules)} structured rules persisted. "
                    f"Total rules in store: {len(updated_rules)}"
                )
            except Exception as e:
                print(f"[AUDITOR] Could not persist structured rules: {e}")

        # Fallback: compile text lessons if no structured_rules returned
        elif report.lessons_learned:
            try:
                from infrastructure.storage.json_lesson_rules import JsonLessonRulesStore
                from core.domain.rules.lessons_compiler import LessonsCompiler
                rules_store = JsonLessonRulesStore()
                updated_rules = rules_store.append_from_lessons(report.lessons_learned)
                print(
                    f"[AUDITOR] Compiled {len(updated_rules)} rules from text lessons."
                )
            except Exception as e:
                print(f"[AUDITOR] Could not compile lesson rules: {e}")

        return report

