"""
Gemini AI Adapter
Connects to Google Generative AI API (gemini-2.0-flash / gemini-1.5-pro)
Token-optimized: compact JSON payloads, concise prompts, async-safe HTTP.
"""
import json
import asyncio
import datetime
import requests
from functools import partial
from typing import Dict, Any, List, Optional
from core.domain.interfaces.ai_engine import IAIEngine
from core.domain.models import SessionConfig, AuditReport, MarketRegime, HTFZone, Significance, PreEntryEvaluation
from infrastructure.ai.mock_ai_adapter import MockAIEngine

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

class GeminiAIAdapter(IAIEngine):
    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model_name = model_name
        self.fallback = MockAIEngine()

    # ─────────────────────────────────────────────────────────────────────────
    # Internal: async-safe HTTP (no event-loop blocking)
    # ─────────────────────────────────────────────────────────────────────────
    async def _post_json(self, payload: dict, timeout: int = 12) -> Optional[str]:
        """Run requests.post in a thread executor to avoid blocking the event loop."""
        url = f"{_GEMINI_BASE}/{self.model_name}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        loop = asyncio.get_event_loop()
        fn = partial(requests.post, url, headers=headers, json=payload, timeout=timeout)
        resp = await loop.run_in_executor(None, fn)
        if resp.status_code == 200:
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        print(f"[GeminiAIAdapter] HTTP {resp.status_code}: {resp.text[:120]}")
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Pre-session plan  (runs once per session)
    # ─────────────────────────────────────────────────────────────────────────
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

        # ── Compact the inputs ────────────────────────────────────────────────
        baseline_summary = {
            k: quant_baseline_metrics[k]
            for k in ("avg_range", "win_rate_by_setup", "avg_rr", "best_hours", "session_count")
            if k in quant_baseline_metrics
        } if isinstance(quant_baseline_metrics, dict) else quant_baseline_metrics

        hi_events = [
            {"time": e.get("datetime_str", ""), "title": e.get("title", ""), "impact": e.get("impact", "")}
            for e in economic_events
            if e.get("impact", "") in ("HIGH", "MEDIUM")
        ][:8]

        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

        prompt = f"""[SYSTEM]
You are the Chief Strategy Engine for a YTC Price Action trading system.
Output ONLY valid JSON matching the schema below. No prose.

[INPUTS]
Symbol: {symbol}
Baseline(100 sessions): {json.dumps(baseline_summary)}
HTF/TTF rates(24h): {json.dumps(recent_rates_json)}
Economic events(high+med only): {json.dumps(hi_events)}
Lessons(RAG): {json.dumps(retrieved_rag_lessons[:3])}

[STEPS]
1. Map major HTF S/R from price rejections.
2. Classify regime: TRENDING_STEADY|TRENDING_WEAKENING|SIDEWAYS_RANGE|BREAKOUT_EXPANSION
3. Enable setups per YTC Matrix (PB/CPB for trend, TST/BOF for range, BPB for breakout).
4. Identify news blackout windows.
5. Output JSON only:

{{"session_id":"sess_YYYYMMDD_HHMMSS","symbol":"{symbol}","generated_at":"{now_utc}","market_regime":"TRENDING_STEADY","htf_zones":{{"resistance_zones":[{{"id":"res_maj_1","high":4350.0,"low":4345.0,"significance":"MAJOR"}}],"support_zones":[{{"id":"sup_maj_1","high":4279.0,"low":4276.0,"significance":"MAJOR"}}]}},"setups_enabled":{{"TST":true,"BOF":true,"BPB":true,"PB":true,"CPB":true,"TREND_BAR_FAIL":true,"INSIDE_BAR_SMA21":true,"ID_NR4":true,"NR7_EMA20":true,"YUM_YUM":true}},"execution_rules":{{"min_rr_ratio_part1":1.0,"require_wholesale_entry":true,"max_entry_timeout_bars_1m":4,"stall_min_candles":3,"scratch_timeout_bars_1m":5,"slippage_tolerance_pips":0.5}},"risk_management":{{"account_risk_limit_percent":1.0,"part1_risk_percent":0.5,"part2_risk_percent":0.5,"session_drawdown_timeout_percent":2.0,"session_drawdown_hardstop_percent":3.0,"business_drawdown_stop_percent":20.0,"max_session_trades":12}},"news_filter":{{"blackout_before_minutes":15,"blackout_after_minutes":15,"high_impact_events":[]}}}}
"""
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }

        try:
            text = await self._post_json(payload, timeout=35)
            if text:
                cfg_data = json.loads(text)
                risk_mgmt = cfg_data.get("risk_management", {})
                risk_mgmt.setdefault("max_session_trades", 12)
                return SessionConfig(
                    session_id=cfg_data.get("session_id", f"sess_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"),
                    symbol=cfg_data.get("symbol", symbol),
                    generated_at=cfg_data.get("generated_at", now_utc),
                    market_regime=MarketRegime(cfg_data["market_regime"]),
                    resistance_zones=[
                        HTFZone(id=z["id"], high=float(z["high"]), low=float(z["low"]), significance=Significance(z.get("significance", "MAJOR")), zone_type="RESISTANCE")
                        for z in cfg_data["htf_zones"]["resistance_zones"]
                    ],
                    support_zones=[
                        HTFZone(id=z["id"], high=float(z["high"]), low=float(z["low"]), significance=Significance(z.get("significance", "MAJOR")), zone_type="SUPPORT")
                        for z in cfg_data["htf_zones"]["support_zones"]
                    ],
                    setups_enabled=cfg_data.get("setups_enabled", {}),
                    execution_rules=cfg_data.get("execution_rules", {}),
                    risk_management=risk_mgmt,
                    news_filter=cfg_data.get("news_filter", {})
                )
        except Exception as e:
            print(f"[GeminiAIAdapter] API call failed: {e}. Falling back to deterministic engine.")

        return await self.fallback.generate_pre_session_plan(
            symbol, quant_baseline_metrics, recent_rates_json, economic_events, retrieved_rag_lessons
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Pre-entry gatekeeper  (HOT PATH — runs before every order)
    # ─────────────────────────────────────────────────────────────────────────
    async def evaluate_candidate_trade(
        self,
        candidate_context: Dict[str, Any],
        trading_config: Dict[str, Any],
        recent_bars: Dict[str, Any]
    ) -> PreEntryEvaluation:
        if not self.api_key:
            return await self.fallback.evaluate_candidate_trade(candidate_context, trading_config, recent_bars)

        # ── Compact the inputs ────────────────────────────────────────────────
        ws = candidate_context.get("wholesale", {})
        # Include all fields the AI needs for YTC evaluation:
        # S1=stop, T1/T2=targets, LWP/LRP=wholesale boundaries, is_valid_entry=pre-check
        wholesale_summary = {
            k: ws[k]
            for k in ("recommended_entry", "LWP", "LRP", "S1", "T1", "T2", "is_valid_entry")
            if k in ws
        } if isinstance(ws, dict) else ws

        zones_raw = candidate_context.get("nearest_zones", [])
        # 6 zones: enough to cover barriers between entry and T2 in most setups
        zones_compact = [
            {"id": z["id"], "type": z.get("type", "S/R"), "h": z["high"], "l": z["low"]}
            for z in zones_raw[:6]
        ]

        # Lessons: structured numeric records {"s":setup,"d":B/S,"r":FC/SC/SO,"rr":float,"bars":int,"ctx":flags}
        # ctx flags: mom_vs=momentum against, stall_to=stall timeout, SL_hit=stopped out, ws_miss=wholesale miss
        lessons_raw = candidate_context.get("session_lessons", [])
        lessons_records = lessons_raw[:5]  # last 5 trades as compact records

        prompt = f"""[SYSTEM]
YTC Price Action Gatekeeper. Approve or veto this trade entry. Output valid JSON only.

[TRADE]
setup={candidate_context.get('setup')} side={candidate_context.get('side')} type={candidate_context.get('order_type')}
entry={candidate_context.get('order_price')} sl={candidate_context.get('sl')} tp1={candidate_context.get('tp1')} tp2={candidate_context.get('tp2')}
wholesale={json.dumps(wholesale_summary)} stall={json.dumps(candidate_context.get('stall_range', {}))}
zones(nearest 6)={json.dumps(zones_compact)}

[SESSION]
regime={trading_config.get('market_regime')} enabled={json.dumps(trading_config.get('setups_enabled', {}))}

[BARS] {json.dumps(recent_bars)}

[SESSION_TRADES] schema:{{s=setup,d=B/S,r=FC/SC/SO,rr=float,bars=int,pnl=float,ctx=flags}}
{json.dumps(lessons_records)}

[RULES]
1.Setup matches regime? 2.Entry inside wholesale w/ R:R>=1? 3.Room to T1 before HTF barrier? 4.No trapped-trader momentum against? 5.Avoids prior session SO/SC patterns?

Output JSON: {{"approved":true,"confidence":0.85,"reason":"tiếng Việt ngắn gọn","concerns":[],"suggested_modifications":{{}}}}"""

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }

        try:
            text = await self._post_json(payload, timeout=5)
            if text:
                r = json.loads(text)
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

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Post-session audit  (runs once per session end)
    # ─────────────────────────────────────────────────────────────────────────
    async def audit_post_session(
        self,
        trading_config_used: Dict[str, Any],
        session_trades_json: List[Dict[str, Any]],
        full_session_ohlcv: Dict[str, Any]
    ) -> AuditReport:
        # Guard: AI audit only worthwhile with enough trades to find patterns
        if not self.api_key or len(session_trades_json) < 3:
            return await self.fallback.audit_post_session(trading_config_used, session_trades_json, full_session_ohlcv)

        session_id = trading_config_used.get("session_id", "sess_audited")
        symbol = trading_config_used.get("symbol", "XAUUSD")

        # ── Compact inputs ────────────────────────────────────────────────────
        trade_summaries = [
            {
                "id": t.get("trade_id", t.get("id", "?")),
                "setup": t.get("setup_name", t.get("setup", "?")),
                "side": t.get("side", "?"),
                "entry": t.get("entry_price", t.get("order_price")),
                "sl": t.get("sl"),
                "tp1": t.get("tp1"),
                "state": t.get("state", "?"),
                "pnl": t.get("pnl", t.get("realized_pnl", None)),
                "rr": t.get("rr_achieved", None),
            }
            for t in session_trades_json
        ]

        cfg_summary = {
            "market_regime": trading_config_used.get("market_regime"),
            "setups_enabled": trading_config_used.get("setups_enabled", {}),
            "risk_management": trading_config_used.get("risk_management", {}),
            "execution_rules": trading_config_used.get("execution_rules", {}),
        }

        # OHLCV: M3 TTF (last 30 bars = ~90 min) for entry-timing audit + M30 for HTF regime check
        ohlcv_compact = {}
        m3_bars = full_session_ohlcv.get("m3", full_session_ohlcv.get("M3", []))
        if m3_bars:
            ohlcv_compact["m3"] = m3_bars[-30:]
        m30_bars = full_session_ohlcv.get("m30", full_session_ohlcv.get("M30", []))
        if m30_bars:
            ohlcv_compact["m30"] = m30_bars[-8:]

        prompt = f"""[SYSTEM]
YTC Price Action Post-Session Auditor. Evaluate trades vs plan. Output JSON only.

[PLAN] {json.dumps(cfg_summary)}

[TRADES] {json.dumps(trade_summaries)}

[OHLCV_SAMPLE] {json.dumps(ohlcv_compact)}

[AUDIT TASKS]
1.Did regime match actual market? Were S/R zones respected?
2.Per trade: entry at wholesale? R:R>=1? Lifecycle managed correctly?
3.Plan flaws: zone placement, regime classification, wholesale buffer.
4.3-5 concise lessons for RAG memory.

Output JSON:
{{"session_id":"{session_id}","compliance_score":0.95,"rule_violations":[{{"trade_id":"...","rule":"...","detail":"..."}}],"plan_critique":{{"regime_accuracy":"...","sr_zones_evaluation":"...","wholesale_engine_assessment":"...","overall_plan_rating":"OPTIMAL","summary":"..."}},"trade_evaluations":[{{"trade_id":"...","setup":"...","side":"...","state":"...","score":1.0,"critique":"..."}}],"parameter_adjustments_suggested":{{"scratch_timeout_bars_1m":5,"min_rr_ratio_part1":1.0}},"lessons_learned":["lesson1","lesson2","lesson3"],"raw_ai_analysis":"Executive summary."}}
"""
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }

        try:
            text = await self._post_json(payload, timeout=35)
            if text:
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

