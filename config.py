"""
Global Configuration for YTC Price Action Trading System (v2.1.0-STRICT)
Avoids common developer ports (3000, 5000, 8000, 8080, etc.)
"""
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any

def _load_env_file(filepath: str = None):
    if not filepath:
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k not in os.environ or not os.environ[k]:
                    os.environ[k] = v

_load_env_file()


@dataclass
class NetworkConfig:
    # Dedicated high-range ports to prevent conflict with other developer services
    SERVER_A_HOST: str = "127.0.0.1"
    SERVER_A_PORT: int = int(os.getenv("SERVER_A_PORT", "29120"))
    
    SERVER_B_HOST: str = "127.0.0.1"
    SERVER_B_PORT: int = int(os.getenv("SERVER_B_PORT", "29121"))
    
    WS_TELEMETRY_PATH: str = "/ws/telemetry"

@dataclass
class BrokerConfig:
    MODE: str = os.getenv("TRADING_MODE", "paper")  # 'live' (MT5) or 'paper' (Simulation)
    MT5_PATH: str = os.getenv("MT5_PATH", "")
    MT5_LOGIN: int = int(os.getenv("MT5_LOGIN", "0"))
    MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
    MT5_SERVER: str = os.getenv("MT5_SERVER", "")
    SYMBOL: str = os.getenv("SYMBOL", "XAUUSD")

    TIMEFRAMES: Dict[str, str] = field(default_factory=lambda: {
        "HTF": "M30",
        "TTF": "M3",
        "LTF": "M1"
    })

@dataclass
class AIConfig:
    PROVIDER: str = os.getenv("AI_PROVIDER", "mock")  # 'gemini', 'openai', 'claude', 'mock'
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    MODEL_NAME: str = os.getenv("AI_MODEL_NAME", "gemini-2.0-flash")

@dataclass
class RiskRulesConfig:
    ACCOUNT_RISK_LIMIT_PERCENT: float = 1.0
    PART1_RISK_PERCENT: float = 0.5
    PART2_RISK_PERCENT: float = 0.5
    SESSION_DRAWDOWN_TIMEOUT_PERCENT: float = 2.0
    SESSION_DRAWDOWN_HARDSTOP_PERCENT: float = 3.0
    BUSINESS_DRAWDOWN_STOP_PERCENT: float = 20.0
    MIN_RR_RATIO_PART1: float = 1.0
    REQUIRE_WHOLESALE_ENTRY: bool = True
    MAX_ENTRY_TIMEOUT_BARS_1M: int = 4
    STALL_MIN_CANDLES: int = 3
    SCRATCH_TIMEOUT_BARS_1M: int = 5
    SLIPPAGE_TOLERANCE_PIPS: float = 1.0

@dataclass
class AppConfig:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    risk: RiskRulesConfig = field(default_factory=RiskRulesConfig)
    base_dir: str = os.path.dirname(os.path.abspath(__file__))

# Global Singleton Config
CONFIG = AppConfig()
