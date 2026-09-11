"""
Gemini AI Adapter
Connects to Google Generative AI API (gemini-2.0-flash / gemini-1.5-pro)
"""
import json
import requests
from typing import Dict, Any, List
from core.domain.interfaces.ai_engine import IAIEngine
from core.domain.models import SessionConfig, AuditReport, MarketRegime, HTFZone, Significance, PreEntryEvaluation
from infrastructure.ai.mock_ai_adapter import MockAIEngine

class GeminiAIAdapter(IAIEngine):
    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model_name = model_name
        self.fallback = MockAIEngine()

    async def generate_pre_session_plan(
        self,
        symbol: str,
        quant_baseline_metrics: Dict[str, Any],
        recent_rates_json: Dict[str, Any],
        economic_events: List[Dict[str, Any]],
        retrieved_rag_lessons: List[str]
    ) -> SessionConfig:
        if not self.api_key:
            return await self.fallback.generate_pre_session_plan(
                symbol, quant_baseline_metrics, recent_rates_json, economic_events, retrieved_rag_lessons
            )

        prompt = f"""
[SYSTEM INSTRUCTION]
You are the Chief Market Risk and Strategy Engine for a YTC Price Action System.
Analyze the provided multi-timeframe OHLCV data and macroeconomic schedule.
Your output must be valid, parseable JSON conforming strictly to the YTCPriceActionSessionConfig schema.
Do not provide prose explanations outside the JSON structure.

[INPUT CONTEXT]
- Symbol: {symbol}
- Historical 100-session baseline: {json.dumps(quant_baseline_metrics)}
- Last 24h HTF (30m) & TTF (3m) Rates: {json.dumps(recent_rates_json)}
- Today's Economic Calendar: {json.dumps(economic_events)}
- Relevant Lessons Learned (Vector RAG): {json.dumps(retrieved_rag_lessons)}

[REQUIRED REASONING STEPS]
1. Map major HTF S/R boundaries using price rejections.
2. Determine market regime: TRENDING_STEADY, TRENDING_WEAKENING, SIDEWAYS_RANGE, or BREAKOUT_EXPANSION.
3. Determine enabled setups based on YTC Matrix:
   - TRENDING_STEADY: Enable PB, CPB. Disable TST.
   - TRENDING_WEAKENING: Enable CPB, BOF (selective). Disable PB.
   - SIDEWAYS_RANGE: Enable TST, BOF. Disable PB.
   - BREAKOUT_EXPANSION: Enable BPB.
4. Calculate news blackout windows around high impact events.
5. Emit complete JSON response.
"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                cfg_data = json.loads(text)
                return SessionConfig(
                    session_id=cfg_data["session_id"],
                    symbol=cfg_data["symbol"],
                    generated_at=cfg_data["generated_at"],
                    market_regime=MarketRegime(cfg_data["market_regime"]),
                    resistance_zones=[
                        HTFZone(id=z["id"], high=z["high"], low=z["low"], significance=Significance(z.get("significance", "MAJOR")), zone_type="RESISTANCE")
                        for z in cfg_data["htf_zones"]["resistance_zones"]
                    ],
                    support_zones=[
                        HTFZone(id=z["id"], high=z["high"], low=z["low"], significance=Significance(z.get("significance", "MAJOR")), zone_type="SUPPORT")
                        for z in cfg_data["htf_zones"]["support_zones"]
                    ],
                    setups_enabled=cfg_data["setups_enabled"],
                    execution_rules=cfg_data["execution_rules"],
                    risk_management=cfg_data["risk_management"],
                    news_filter=cfg_data["news_filter"]
                )
        except Exception as e:
            print(f"[GeminiAIAdapter] API call failed: {e}. Falling back to deterministic engine.")

        return await self.fallback.generate_pre_session_plan(
            symbol, quant_baseline_metrics, recent_rates_json, economic_events, retrieved_rag_lessons
        )

    async def evaluate_candidate_trade(
        self,
        candidate_context: Dict[str, Any],
        trading_config: Dict[str, Any],
        recent_bars: Dict[str, Any]
    ) -> PreEntryEvaluation:
        if not self.api_key:
            return await self.fallback.evaluate_candidate_trade(candidate_context, trading_config, recent_bars)

        prompt = f"""
[SYSTEM INSTRUCTION]
You are the Senior Price Action Risk Gatekeeper specializing in Lance Beggs YTC Methodology.
A trade setup trigger has been detected by the deterministic execution engine.
Evaluate the candidate trade context against current market conditions.
Decide whether to APPROVE (true) or VETO (false) this trade entry.

[CANDIDATE TRADE CONTEXT]
- Setup: {candidate_context.get('setup')}
- Side: {candidate_context.get('side')}
- Order Type: {candidate_context.get('order_type')}
- Proposed Entry Price: {candidate_context.get('order_price')}
- Proposed Stop Loss: {candidate_context.get('sl')}
- Proposed TP1: {candidate_context.get('tp1')}
- Proposed TP2: {candidate_context.get('tp2')}
- Wholesale Calculation: {json.dumps(candidate_context.get('wholesale', {}))}
- Micro-Stall Range: {json.dumps(candidate_context.get('stall_range', {}))}
- Nearest HTF S/R Zones: {json.dumps(candidate_context.get('nearest_zones', []))}

[SESSION TRADING PLAN]
- Market Regime: {trading_config.get('market_regime')}
- Setups Enabled: {json.dumps(trading_config.get('setups_enabled', {}))}

[RECENT BARS CONTEXT]
{json.dumps(recent_bars, indent=2)}

[EVALUATION RULES]
1. Does this setup match the active Market Regime?
2. Is entry inside the Wholesale zone with Part 1 R:R >= 1.0?
3. Is there sufficient room to move to T1 before running into major HTF barriers?
4. Are trapped traders visible or is momentum pushing against this entry?

[OUTPUT FORMAT]
Reply in valid JSON only:
{{
  "approved": true,
  "confidence": 0.85,
  "reason": "Giải thích ngắn gọn bằng tiếng Việt lý do phê duyệt hoặc từ chối.",
  "concerns": ["Điểm lưu ý hoặc rủi ro tiềm ẩn"],
  "suggested_modifications": {{}}
}}
"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                r = json.loads(text)
                import datetime
                return PreEntryEvaluation(
                    approved=bool(r.get("approved", True)),
                    confidence=float(r.get("confidence", 0.8)),
                    reason=str(r.get("reason", "Phê duyệt bởi Gemini AI")),
                    concerns=list(r.get("concerns", [])),
                    suggested_modifications=dict(r.get("suggested_modifications", {})),
                    evaluated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    model_name=self.model_name
                )
        except Exception as e:
            print(f"[GeminiAIAdapter] Pre-entry evaluation API failed: {e}. Falling back to deterministic engine.")

        return await self.fallback.evaluate_candidate_trade(candidate_context, trading_config, recent_bars)

    async def audit_post_session(
        self,
        trading_config_used: Dict[str, Any],
        session_trades_json: List[Dict[str, Any]],
        full_session_ohlcv: Dict[str, Any]
    ) -> AuditReport:
        if not self.api_key:
            return await self.fallback.audit_post_session(trading_config_used, session_trades_json, full_session_ohlcv)

        session_id = trading_config_used.get("session_id", "sess_audited")
        symbol = trading_config_used.get("symbol", "XAUUSD")

        prompt = f"""
[SYSTEM INSTRUCTION]
You are the Chief Quantitative Auditor and Master Trading Coach specializing in Lance Beggs YTC Price Action Methodology.
Conduct a rigorous Post-Session Hindsight Audit evaluating actual trades against the session trading plan.

[INPUT CONTEXT]
1. TRADING PLAN USED:
{json.dumps(trading_config_used, indent=2)}

2. COMPLETED TRADES LOG (WITH ENTRY CONTEXT & OUTCOMES):
{json.dumps(session_trades_json, indent=2)}

3. RECENT OHLCV CONTEXT (IF AVAILABLE):
{json.dumps(full_session_ohlcv, indent=2)}

[LANCE BEGGS 4-QUESTION AUDIT REQUIREMENTS]
1. Expectation vs Reality:
   - Compare market behavior against planned market regime ({trading_config_used.get('market_regime')}).
   - Assess HTF Support & Resistance zones: Did price respect them, false-breakout, or slice through?
2. Execution vs Plan:
   - For EACH trade in the trade log, evaluate its ENTRY CONTEXT: Did it respect Wholesale pricing (LWP/LRP)? Was it entered at wholesale value with R:R >= 1.0?
   - Did the trade setup conform to the enabled setup matrix for the regime?
   - Did the trader/system manage lifecycle properly (T1 partial profit, trailing SL, scratch when premise threatened)?
3. Trading Plan Critique:
   - Evaluate whether the session plan parameters (S/R zones, regime classification, setup selection, Wholesale buffer) were optimal.
   - Point out any flaws in how the plan was calculated.
4. Actionable Improvements & Lessons Learned:
   - Propose specific parameter adjustments (e.g. scratch timeout, minimum R:R, zone proximity).
   - Formulate 3-5 concise, high-value lessons learned to store in the Vector RAG memory for future planning.

[OUTPUT FORMAT]
You must reply with valid JSON conforming strictly to this JSON structure:
{{
  "session_id": "{session_id}",
  "compliance_score": 0.95,
  "rule_violations": [
    {{"trade_id": "...", "rule": "...", "detail": "..."}}
  ],
  "plan_critique": {{
    "regime_accuracy": "...",
    "sr_zones_evaluation": "...",
    "wholesale_engine_assessment": "...",
    "overall_plan_rating": "OPTIMAL / NEEDS_ADJUSTMENT / FLAWED",
    "summary": "..."
  }},
  "trade_evaluations": [
    {{
      "trade_id": "...",
      "setup": "...",
      "side": "...",
      "state": "...",
      "score": 1.0,
      "critique": "..."
    }}
  ],
  "parameter_adjustments_suggested": {{
    "scratch_timeout_bars_1m": 5,
    "min_rr_ratio_part1": 1.0,
    "post_trade_cooldown_seconds": 180
  }},
  "lessons_learned": [
    "Concise lesson 1",
    "Concise lesson 2",
    "Concise lesson 3"
  ],
  "raw_ai_analysis": "Executive summary paragraph of the audit."
}}
"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=35)
            if resp.status_code == 200:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                r = json.loads(text)
                return AuditReport(
                    session_id=r.get("session_id", session_id),
                    compliance_score=float(r.get("compliance_score", 1.0)),
                    rule_violations=r.get("rule_violations", []),
                    hindsight_optimal_trades=[],
                    lessons_learned=r.get("lessons_learned", []),
                    parameter_adjustments_suggested=r.get("parameter_adjustments_suggested", {}),
                    plan_critique=r.get("plan_critique"),
                    trade_evaluations=r.get("trade_evaluations", []),
                    raw_ai_analysis=r.get("raw_ai_analysis", "")
                )
        except Exception as e:
            print(f"[GeminiAIAdapter] Audit API call failed: {e}. Falling back to deterministic engine.")

        return await self.fallback.audit_post_session(trading_config_used, session_trades_json, full_session_ohlcv)
