"""
Global Configuration for YTC Price Action Trading System (v2.1.0-STRICT)
Avoids common developer ports (3000, 5000, 8000, 8080, etc.)
"""
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACTIVE_ENV_FILE: str = os.path.join(BASE_DIR, ".env")

def _parse_env_file(filepath: str, override: bool = True):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if override or (k not in os.environ or not os.environ[k]):
                    os.environ[k] = v

def load_env(env_name_or_path: Optional[str] = None, mode: Optional[str] = None) -> str:
    """
    Loads environment variables based on specific file or deployment mode.
    Priority:
      1. Specific file path if passed in env_name_or_path (e.g. 'd:/path/.env.custom')
      2. Specific mode name (e.g. 'paper' -> .env.paper, 'live' -> .env.live)
      3. Fallback: .env.local -> .env
    Returns the absolute path of the active env file.
    """
    global ACTIVE_ENV_FILE

    target_file = None

    if env_name_or_path:
        # Check if direct file path exists
        if os.path.isfile(env_name_or_path):
            target_file = os.path.abspath(env_name_or_path)
        elif os.path.isfile(os.path.join(BASE_DIR, env_name_or_path)):
            target_file = os.path.join(BASE_DIR, env_name_or_path)
        elif os.path.isfile(os.path.join(BASE_DIR, f".env.{env_name_or_path}")):
            target_file = os.path.join(BASE_DIR, f".env.{env_name_or_path}")

    if not target_file and mode:
        candidate = os.path.join(BASE_DIR, f".env.{mode}")
        if os.path.isfile(candidate):
            target_file = candidate

    if not target_file:
        # Check default files in priority order
        for name in [".env.local", ".env"]:
            candidate = os.path.join(BASE_DIR, name)
            if os.path.isfile(candidate):
                target_file = candidate
                break

    if not target_file:
        target_file = os.path.join(BASE_DIR, ".env")

    ACTIVE_ENV_FILE = target_file
    if os.path.isfile(target_file):
        _parse_env_file(target_file, override=True)

    return ACTIVE_ENV_FILE

# Initial load
load_env()


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
    MT5_LOGIN: int = int(os.getenv("MT5_LOGIN", "0") or "0")
    MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
    MT5_SERVER: str = os.getenv("MT5_SERVER", "")
    SYMBOL: str = os.getenv("SYMBOL", "XAUUSD")

    TIMEFRAMES: Dict[str, str] = field(default_factory=lambda: {
        "HTF": "M15",
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
    MIN_RR_RATIO_PART1: float = 0.75   # Tỷ lệ R:R tối thiểu cho Part 1 theo Lance Beggs YTC
    SL_MULTIPLIER: float = 1.20   # Nới rộng SL lên 120%
    TP_MULTIPLIER: float = 0.90   # Thu hẹp TP còn 90%
    REQUIRE_WHOLESALE_ENTRY: bool = True
    MAX_ENTRY_TIMEOUT_BARS_1M: int = 4
    STALL_MIN_CANDLES: int = 3
    SCRATCH_TIMEOUT_BARS_1M: int = 5
    SLIPPAGE_TOLERANCE_PIPS: float = 1.0
    MIN_PROFIT_USD: float = 2.0   # Lợi nhuận tối thiểu $2.00 cho mỗi lệnh 0.01 lot
    ENABLE_SL_TRAILING: bool = False       # Tắt hoàn toàn việc dời SL tự động theo giá
    ENABLE_EARLY_PROFIT_LOCK: bool = False # Tắt dời SL sớm ở IN_POSITION
    EARLY_LOCK_MIN_USD: float = 2.0
    EARLY_LOCK_MIN_R: float = 0.5
    CANDLE_SL_MULTIPLIER: float = 1.20    # Hệ số nhân biên độ nến gần nhất để tính sàn SL động (1.2x biên độ nến)
    CANDLE_BUFFER_RATIO: float = 0.25     # Tỷ lệ đệm vượt ngoài đáy/đỉnh nến gần nhất (25% biên độ nến)

@dataclass
class ProfitProtectionConfig:
    """
    Cơ chế bảo vệ lợi nhuận chủ động cho Part 2 sau khi chạm T1.
    Tối ưu chuyên sâu cho XAUUSD (Gold): Chốt lệnh theo mô hình nến, không dời SL gây quét non.
    """
    ENABLED: bool = True
    MIN_PROFIT_USD: float = 2.0   # Lợi nhuận tối thiểu $2.00 cho mỗi 0.01 lot
    ENABLE_SL_TRAILING: bool = False       # Tắt dời SL của Part 2 (giữ SL cố định làm hard disaster stop)
    ENABLE_RATCHET_SL: bool = False        # Tắt nâng SL theo bậc thang R
    ENABLE_SWING_TRAILING: bool = False    # Tắt kéo SL theo Swing M1/M3
    ENABLE_TIME_LOCK: bool = False         # Tắt dời SL theo thời gian
    
    # Candlestick Exit Engine (Chốt lệnh chủ động theo dấu hiệu nến Price Action)
    ENABLE_CANDLESTICK_EXITS: bool = True  # Bật chốt lệnh chủ động theo nến đảo chiều
    CANDLESTICK_EXIT_MIN_HOLDING_BARS: int = 1
    CANDLESTICK_EXIT_MIN_R: float = 0.5    # Ngưỡng R tối thiểu để chốt nến khi có lãi (>= 0.5R)

    # Lớp 1: Dynamic R-Multiple Ratchet (Dành cho khi ENABLE_RATCHET_SL = True)
    RATCHET_LEVELS: List[Dict[str, float]] = field(default_factory=lambda: [
        {"level": 1, "min_r": 0.8, "lock_r": 0.2},
        {"level": 2, "min_r": 1.2, "lock_r": 0.5},
        {"level": 3, "min_r": 1.8, "lock_r": 1.0},
        {"level": 4, "min_r": 2.5, "lock_r": 1.8},
    ])
    
    # Lớp 2: Momentum Reversal Guard (Chỉ thoát khi đảo chiều thật sự, không cắt nhịp pullback lành mạnh)
    ENABLE_MOMENTUM_GUARD: bool = True
    MOMENTUM_ATR_PERIOD: int = 14
    MOMENTUM_BAR_ATR_MULT: float = 1.6      # Nến M1 ngược chiều >= 1.6 * ATR(14)
    MOMENTUM_REVERSAL_RETRACE_PCT: float = 0.50  # Trả lại >= 50% lợi nhuận đỉnh Part 2
    MIN_PEAK_R_FOR_GUARD: float = 1.2       # Chỉ bật Guard nếu Part 2 từng chạm >= +1.2R
    
    # Lớp 3: Adaptive Time-Based Profit Lock (Vàng đi ngang quá lâu không bứt phá tới T2)
    TIME_LOCK_BARS: int = 20                # 20 nến M1 (20 phút) ở trạng thái trailing
    TIME_LOCK_MIN_R: float = 0.8            # Nếu đang có >= +0.8R
    TIME_LOCK_SECURE_R: float = 0.5         # Thì nâng SL khóa ít nhất +0.5R


@dataclass
class PWESConfig:
    """Probability-Weighted Entry System - Cấu hình 5 module mới"""
    ENABLED: bool = True

    # Module 1: Session-Aware Probability Layer
    ENABLE_SESSION_FILTER: bool = True
    DEAD_ZONE_START_HOUR: int = 3   # UTC+7
    DEAD_ZONE_END_HOUR: int = 7     # UTC+7

    # Module 2: Confluence Probability Score
    ENABLE_CONFLUENCE_FILTER: bool = True
    MIN_CONFLUENCE_SIGNALS: int = 3  # Tối thiểu 3/5 tín hiệu

    # Module 3: Dynamic Lot Sizer
    ENABLE_DYNAMIC_LOT: bool = True
    MIN_NET_PROFIT_USD: float = 2.0   # Mục tiêu lợi nhuận tối thiểu
    EST_COMMISSION_PER_LOT: float = 7.0  # USD/lot round-trip
    EST_SPREAD_POINTS: float = 0.50      # Points spread XAUUSD
    MAX_LOT_SIZE: float = 1.0

    # Module 4: Probability-Based Exit
    ENABLE_PROB_EXIT: bool = True
    EXIT_HOLD_THRESHOLD: float = 0.70
    EXIT_BREAKEVEN_THRESHOLD: float = 0.50
    EXIT_PROFIT_THRESHOLD: float = 0.30

    # Module 5: Kelly Criterion Anti-Revenge Lot Scaling
    ENABLE_KELLY_SCALING: bool = True
    KELLY_LOOKBACK_TRADES: int = 20   # Dùng 20 lệnh gần nhất để ước tính win rate
    MAX_CONSECUTIVE_LOSSES_SKIP: int = 5
    MAX_CONSECUTIVE_LOSSES_REDUCE: int = 3


@dataclass
class AppConfig:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    risk: RiskRulesConfig = field(default_factory=RiskRulesConfig)
    profit_protection: ProfitProtectionConfig = field(default_factory=ProfitProtectionConfig)
    pwes: PWESConfig = field(default_factory=PWESConfig)
    base_dir: str = BASE_DIR

def reload_config(env_name_or_path: Optional[str] = None, mode: Optional[str] = None) -> AppConfig:
    """Reloads environment variables from file and creates a fresh AppConfig instance."""
    global CONFIG
    load_env(env_name_or_path, mode)
    CONFIG = AppConfig(
        network=NetworkConfig(
            SERVER_A_HOST=os.getenv("SERVER_A_HOST", "127.0.0.1"),
            SERVER_A_PORT=int(os.getenv("SERVER_A_PORT", "29120")),
            SERVER_B_HOST=os.getenv("SERVER_B_HOST", "127.0.0.1"),
            SERVER_B_PORT=int(os.getenv("SERVER_B_PORT", "29121")),
        ),
        broker=BrokerConfig(
            MODE=os.getenv("TRADING_MODE", "paper"),
            MT5_PATH=os.getenv("MT5_PATH", ""),
            MT5_LOGIN=int(os.getenv("MT5_LOGIN", "0") or "0"),
            MT5_PASSWORD=os.getenv("MT5_PASSWORD", ""),
            MT5_SERVER=os.getenv("MT5_SERVER", ""),
            SYMBOL=os.getenv("SYMBOL", "XAUUSD"),
        ),
        ai=AIConfig(
            PROVIDER=os.getenv("AI_PROVIDER", "mock"),
            GEMINI_API_KEY=os.getenv("GEMINI_API_KEY", ""),
            OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
            ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", ""),
            MODEL_NAME=os.getenv("AI_MODEL_NAME", "gemini-2.0-flash"),
        ),
        risk=RiskRulesConfig(),
        profit_protection=ProfitProtectionConfig(),
        pwes=PWESConfig(),
        base_dir=BASE_DIR
    )
    return CONFIG

# Global Singleton Config
CONFIG = reload_config()

