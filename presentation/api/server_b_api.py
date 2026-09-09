"""
Server B FastAPI Application (AI Strategy, Evaluation & Audit Studio)
Port: 29121 (Custom port avoiding common developer ports)
"""
import os
import requests
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.use_cases.intelligence.pre_session_planner import PreSessionPlannerUseCase
from core.use_cases.intelligence.hindsight_auditor import HindsightAuditorUseCase
from core.use_cases.intelligence.self_optimizer import SelfOptimizerUseCase
from core.domain.interfaces.vector_store import IVectorStore
from infrastructure.storage.json_store import LocalJsonStore
from infrastructure.ai.gemini_adapter import GeminiAIAdapter
from infrastructure.ai.openai_adapter import OpenAIAdapter

class PlanRequest(BaseModel):
    symbol: str = "XAUUSD"
    economic_events: List[Dict[str, Any]] = []

class AuditRequest(BaseModel):
    trading_config: Dict[str, Any]
    session_trades: List[Dict[str, Any]]
    full_session_ohlcv: Dict[str, Any] = {}

class AIConfigRequest(BaseModel):
    provider: str = "gemini"  # "gemini" or "openai"
    api_key: str = ""
    model_name: str = "gemini-2.0-flash"

class FetchModelsRequest(BaseModel):
    provider: str = "gemini"
    api_key: Optional[str] = ""

def create_server_b_app(
    planner: PreSessionPlannerUseCase,
    auditor: HindsightAuditorUseCase,
    vector_store: IVectorStore,
    json_store: LocalJsonStore
) -> FastAPI:
    app = FastAPI(title="Server B - AI Evaluation & Planning Studio", version="2.1.0-STRICT")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # State for current AI provider & keys
    current_ai = {
        "provider": os.getenv("AI_PROVIDER", "gemini"),
        "model_name": os.getenv("AI_MODEL_NAME", "gemini-2.0-flash"),
        "has_gemini_key": bool(os.getenv("GEMINI_API_KEY", "")),
        "has_openai_key": bool(os.getenv("OPENAI_API_KEY", ""))
    }

    @app.get("/api/status")
    async def get_status():
        return {
            "server": "Server B (AI Brain & Audit)",
            "port": 29121,
            "status": "RUNNING",
            "ai_config": current_ai,
            "capabilities": ["PreSessionPlanning", "PostSessionHindsightAudit", "VectorRAG", "DynamicOptimization"]
        }

    @app.get("/api/ai_config")
    async def get_ai_config():
        masked_key = ""
        key_set = False
        if current_ai["provider"] == "gemini":
            k = os.getenv("GEMINI_API_KEY", "")
            if k:
                masked_key = k[:6] + "..." + k[-4:] if len(k) > 10 else "***"
                key_set = True
        elif current_ai["provider"] == "openai":
            k = os.getenv("OPENAI_API_KEY", "")
            if k:
                masked_key = k[:6] + "..." + k[-4:] if len(k) > 10 else "***"
                key_set = True

        return {
            "provider": current_ai["provider"],
            "model_name": current_ai["model_name"],
            "is_key_set": key_set,
            "masked_key": masked_key
        }

    @app.post("/api/ai_config")
    async def set_ai_config(req: AIConfigRequest):
        provider = req.provider.lower()
        key = req.api_key.strip()
        model = req.model_name.strip()

        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")

        if provider == "gemini":
            os.environ["AI_PROVIDER"] = "gemini"
            os.environ["AI_MODEL_NAME"] = model or "gemini-2.0-flash"
            if key:
                os.environ["GEMINI_API_KEY"] = key
            new_engine = GeminiAIAdapter(api_key=key or os.getenv("GEMINI_API_KEY", ""), model_name=model or "gemini-2.0-flash")
            planner.ai_engine = new_engine
            auditor.ai_engine = new_engine
            current_ai["provider"] = "gemini"
            current_ai["model_name"] = model or "gemini-2.0-flash"
            current_ai["has_gemini_key"] = bool(key or os.getenv("GEMINI_API_KEY", ""))

        elif provider == "openai":
            os.environ["AI_PROVIDER"] = "openai"
            os.environ["AI_MODEL_NAME"] = model or "gpt-4o"
            if key:
                os.environ["OPENAI_API_KEY"] = key
            new_engine = OpenAIAdapter(api_key=key or os.getenv("OPENAI_API_KEY", ""), model_name=model or "gpt-4o")
            planner.ai_engine = new_engine
            auditor.ai_engine = new_engine
            current_ai["provider"] = "openai"
            current_ai["model_name"] = model or "gpt-4o"
            current_ai["has_openai_key"] = bool(key or os.getenv("OPENAI_API_KEY", ""))

        # Update .env file on disk
        try:
            with open(env_file, "w", encoding="utf-8") as f:
                f.write(f"AI_PROVIDER={current_ai['provider']}\n")
                f.write(f"GEMINI_API_KEY={os.getenv('GEMINI_API_KEY', '')}\n")
                f.write(f"OPENAI_API_KEY={os.getenv('OPENAI_API_KEY', '')}\n")
                f.write(f"AI_MODEL_NAME={current_ai['model_name']}\n")
                f.write(f"SYMBOL={os.getenv('SYMBOL', 'XAUUSD')}\n")
        except Exception as e:
            print(f"[Server B] Could not save .env: {e}")

        return {
            "status": "SUCCESS",
            "message": f"AI Engine updated to {provider.upper()} ({current_ai['model_name']})",
            "provider": current_ai["provider"],
            "model_name": current_ai["model_name"],
            "has_key": bool(key)
        }

    @app.post("/api/ai_models/fetch")
    async def fetch_ai_models(req: FetchModelsRequest):
        provider = req.provider.lower()
        key = req.api_key.strip() or os.getenv(f"{provider.upper()}_API_KEY", "")
        models_list = []
        source = "online_api"

        if provider == "gemini":
            if key:
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
                    resp = requests.get(url, timeout=12)
                    if resp.status_code == 200:
                        data = resp.json()
                        for m in data.get("models", []):
                            name = m.get("name", "").replace("models/", "")
                            display = m.get("displayName", name)
                            methods = m.get("supportedGenerationMethods", [])
                            if "generateContent" in methods:
                                models_list.append({
                                    "id": name,
                                    "display": f"{name} ({display})"
                                })
                except Exception as e:
                    print(f"[Server B] Online query to Google Gemini failed: {e}")

            if not models_list:
                source = "fallback_curated"
                models_list = [
                    {"id": "gemini-2.0-flash", "display": "gemini-2.0-flash (Siêu tốc <1.5s - Khuyên dùng)"},
                    {"id": "gemini-2.5-pro", "display": "gemini-2.5-pro (Thế hệ 2.5 - Suy luận chuyên sâu)"},
                    {"id": "gemini-2.5-flash", "display": "gemini-2.5-flash (Thế hệ 2.5 - Nhanh & Thông minh)"},
                    {"id": "gemini-2.0-flash-lite", "display": "gemini-2.0-flash-lite (Siêu nhẹ, tiết kiệm)"},
                    {"id": "gemini-2.0-flash-thinking-exp-01-21", "display": "gemini-2.0-flash-thinking (Tư duy chuỗi)"},
                    {"id": "gemini-2.0-pro-exp-02-05", "display": "gemini-2.0-pro-exp (Bản Pro thử nghiệm)"},
                    {"id": "gemini-1.5-pro", "display": "gemini-1.5-pro (Ổn định, Context 2M)"},
                    {"id": "gemini-1.5-flash", "display": "gemini-1.5-flash (Ổn định tiêu chuẩn)"},
                    {"id": "gemini-1.5-flash-8b", "display": "gemini-1.5-flash-8b (Bản nhỏ)"}
                ]

        elif provider == "openai":
            if key:
                try:
                    url = "https://api.openai.com/v1/models"
                    headers = {"Authorization": f"Bearer {key}"}
                    resp = requests.get(url, headers=headers, timeout=12)
                    if resp.status_code == 200:
                        data = resp.json()
                        raw_models = [m.get("id", "") for m in data.get("data", [])]
                        wanted_prefixes = ("gpt-4", "gpt-3.5", "o1", "o3", "chatgpt")
                        filtered = [m for m in raw_models if any(m.startswith(p) for p in wanted_prefixes)]
                        filtered.sort(reverse=True)
                        for m in filtered:
                            models_list.append({"id": m, "display": m})
                except Exception as e:
                    print(f"[Server B] Online query to OpenAI failed: {e}")

            if not models_list:
                source = "fallback_curated"
                models_list = [
                    {"id": "gpt-4o", "display": "gpt-4o (Đa phương thức flagship)"},
                    {"id": "gpt-4o-mini", "display": "gpt-4o-mini (Nhanh & Tiết kiệm)"},
                    {"id": "o3-mini", "display": "o3-mini (Suy luận logic thế hệ mới)"},
                    {"id": "o1", "display": "o1 (Suy luận chuyên sâu)"},
                    {"id": "o1-mini", "display": "o1-mini (Suy luận nhanh)"},
                    {"id": "gpt-4-turbo", "display": "gpt-4-turbo"}
                ]

        return {
            "status": "SUCCESS",
            "provider": provider,
            "source": source,
            "count": len(models_list),
            "models": models_list
        }

    @app.post("/api/plan")
    async def generate_plan(req: PlanRequest):
        try:
            config = await planner.execute(symbol=req.symbol, economic_events=req.economic_events)
            config_dict = {
                "session_id": config.session_id,
                "symbol": config.symbol,
                "generated_at": config.generated_at,
                "market_regime": config.market_regime.value,
                "htf_zones": {
                    "resistance_zones": [{"id": z.id, "high": z.high, "low": z.low, "significance": z.significance.value} for z in config.resistance_zones],
                    "support_zones": [{"id": z.id, "high": z.high, "low": z.low, "significance": z.significance.value} for z in config.support_zones]
                },
                "setups_enabled": config.setups_enabled,
                "execution_rules": config.execution_rules,
                "risk_management": config.risk_management,
                "news_filter": config.news_filter
            }
            json_store.save_session_config(config_dict)
            return {"status": "SUCCESS", "config": config_dict}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/audit")
    async def execute_audit(req: AuditRequest):
        try:
            report = await auditor.execute(
                trading_config_used=req.trading_config,
                session_trades_json=req.session_trades,
                full_session_ohlcv=req.full_session_ohlcv
            )
            return {
                "status": "SUCCESS",
                "audit_report": report.__dict__
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/rag/lessons")
    async def get_lessons(query: str = "general", regime: str = "GENERAL", limit: int = 5):
        lessons = await vector_store.search_lessons(query=query, regime=regime, limit=limit)
        return {"lessons": lessons}

    return app
