"""
OpenAI & Compatible API Adapter (GPT-4o, Claude, DeepSeek, etc.)
"""
import json
import requests
from typing import Dict, Any, List
from core.domain.interfaces.ai_engine import IAIEngine
from core.domain.models import SessionConfig, AuditReport, MarketRegime, HTFZone, Significance
from infrastructure.ai.mock_ai_adapter import MockAIEngine

class OpenAIAdapter(IAIEngine):
    def __init__(self, api_key: str, model_name: str = "gpt-4o", base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
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
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": "You are a quantitative trading price action strategist. Always reply with strict JSON matching the schema."},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"}
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                cfg_data = json.loads(content)
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
            print(f"[OpenAIAdapter] API call failed: {e}. Falling back to deterministic engine.")

        return await self.fallback.generate_pre_session_plan(
            symbol, quant_baseline_metrics, recent_rates_json, economic_events, retrieved_rag_lessons
        )

    async def audit_post_session(
        self,
        trading_config_used: Dict[str, Any],
        session_trades_json: List[Dict[str, Any]],
        full_session_ohlcv: Dict[str, Any]
    ) -> AuditReport:
        if not self.api_key:
            return await self.fallback.audit_post_session(trading_config_used, session_trades_json, full_session_ohlcv)

        return await self.fallback.audit_post_session(trading_config_used, session_trades_json, full_session_ohlcv)
