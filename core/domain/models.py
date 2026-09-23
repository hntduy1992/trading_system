"""
Core Domain Models & Data Schemas
Conforming strictly to YTC Price Action Trader (v2.1.0-STRICT)
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime

class MarketRegime(str, Enum):
    TRENDING_STEADY = "TRENDING_STEADY"
    TRENDING_WEAKENING = "TRENDING_WEAKENING"
    SIDEWAYS_RANGE = "SIDEWAYS_RANGE"
    BREAKOUT_EXPANSION = "BREAKOUT_EXPANSION"
    CHOPPY_NO_TRADE = "CHOPPY_NO_TRADE"

class SwingType(str, Enum):
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"

class Significance(str, Enum):
    MAJOR = "MAJOR"
    MINOR = "MINOR"

class SetupType(str, Enum):
    TST = "TST"   # Test of Support / Resistance
    BOF = "BOF"   # Breakout Failure
    BPB = "BPB"   # Breakout Pullback
    PB = "PB"     # Pullback
    CPB = "CPB"   # Complex Pullback
    # Vol 5 Price Action Setups
    TREND_BAR_FAIL = "TREND_BAR_FAIL"
    INSIDE_BAR_SMA21 = "INSIDE_BAR_SMA21"
    ID_NR4 = "ID_NR4"
    NR7_EMA20 = "NR7_EMA20"
    YUM_YUM = "YUM_YUM"

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class PositionState(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    IN_POSITION = "IN_POSITION"
    T1_HIT = "T1_HIT"
    TRAILING_STOP = "TRAILING_STOP"
    SCRATCHED = "SCRATCHED"
    STOPPED_OUT = "STOPPED_OUT"
    FULLY_CLOSED = "FULLY_CLOSED"

@dataclass
class Bar:
    timestamp: float       # Unix timestamp in seconds
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    timeframe: str = "M1"  # "M1", "M3", "M30"

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def high_wick(self) -> float:
        return self.high

    @property
    def low_wick(self) -> float:
        return self.low

    @property
    def high_body(self) -> float:
        return max(self.open, self.close)

    @property
    def low_body(self) -> float:
        return min(self.open, self.close)

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

@dataclass
class SwingNode:
    swing_type: SwingType
    price: float
    time: float
    bar_index: int
    bar: Bar

@dataclass
class VectorDynamics:
    momentum: float    # dPrice / dTime
    projection: float  # |Extreme_current - Extreme_previous|
    depth: float       # (|Retracement| / |Extension|) * 100%

@dataclass
class HTFZone:
    id: str
    high: float
    low: float
    significance: Significance = Significance.MAJOR
    zone_type: str = "RESISTANCE"  # "RESISTANCE" or "SUPPORT"

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high

@dataclass
class WholesaleCalculation:
    setup_type: SetupType
    side: OrderSide
    S1: float              # Stop loss (technical invalidation)
    T1: float              # Target 1 (nearest TTF opposing swing)
    T2: float              # Target 2 (next major HTF S/R boundary)
    LWP: float             # Last Wholesale Price (breakout trigger of 1m stall/spring)
    LRP: float             # Last Reward:Risk Price (boundary preserving R:R >= 1.0 for Part 1)
    is_valid_entry: bool   # Long: Entry <= min(LWP, LRP); Short: Entry >= max(LWP, LRP)
    recommended_entry: float
    rr_ratio_part1: float = 0.0
    min_rr_ratio: float = 1.0
    sl_multiplier: float = 1.0
    tp_multiplier: float = 1.0

@dataclass
class PositionPart:
    part_number: int       # 1 or 2
    lot_size: float
    entry_price: float
    sl_price: float
    tp_price: float
    ticket: int = 0
    is_closed: bool = False
    close_price: Optional[float] = None
    pnl: float = 0.0

@dataclass
class TradeLifecycle:
    trade_id: str
    symbol: str
    setup_type: SetupType
    side: OrderSide
    state: PositionState
    part1: PositionPart
    part2: PositionPart
    open_time: float
    limit_order_ticket: Optional[int] = None
    stop_order_ticket: Optional[int] = None
    m1_bars_in_trade: int = 0
    last_bar_timestamp: Optional[float] = None
    anchor_id: Optional[str] = None
    close_time: Optional[float] = None
    spatial_anchor_key: Optional[str] = None
    max_bars_pending: Optional[int] = None
    entry_context: Optional[Dict[str, Any]] = None
    close_context: Optional[Dict[str, Any]] = None
    profit_protection_level: int = 0
    max_unrealized_r_part2: float = 0.0
    profit_protection_events: List[Dict[str, Any]] = field(default_factory=list)
    initial_risk_dist: float = 0.0
    bars_in_trailing: int = 0
    last_trailing_bar_timestamp: Optional[float] = None
    early_profit_locked: bool = False

    @property
    def total_pnl(self) -> float:
        return round((self.part1.pnl or 0.0) + (self.part2.pnl or 0.0), 2)

    @property
    def total_volume(self) -> float:
        return round((self.part1.lot_size or 0.0) + (self.part2.lot_size or 0.0), 2)

@dataclass
class SessionConfig:
    session_id: str
    symbol: str
    generated_at: str
    market_regime: MarketRegime
    resistance_zones: List[HTFZone]
    support_zones: List[HTFZone]
    setups_enabled: Dict[str, bool]
    execution_rules: Dict[str, Any]
    risk_management: Dict[str, Any]
    news_filter: Dict[str, Any]
    session_tag: Optional[str] = None

@dataclass
class AuditReport:
    session_id: str
    compliance_score: float
    rule_violations: List[Dict[str, Any]] = field(default_factory=list)
    hindsight_optimal_trades: List[Dict[str, Any]] = field(default_factory=list)
    lessons_learned: List[str] = field(default_factory=list)
    parameter_adjustments_suggested: Dict[str, Any] = field(default_factory=dict)
    plan_critique: Optional[Dict[str, Any]] = None
    trade_evaluations: List[Dict[str, Any]] = field(default_factory=list)
    raw_ai_analysis: str = ""
    # Machine-parseable rules compiled from lessons — dùng cho Layer 2 Scorer
    # Format: [{"condition": {"setup": "PB", "regime": "..."}, "action": "PENALIZE", "delta": -0.25, "reason": "..."}]
    structured_rules: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class PreEntryEvaluation:
    approved: bool
    confidence: float
    reason: str
    concerns: List[str] = field(default_factory=list)
    suggested_modifications: Dict[str, Any] = field(default_factory=dict)
    evaluated_at: str = ""
    model_name: str = ""

@dataclass
class InstrumentProfile:
    symbol: str
    digits: int
    point: float
    contract_size: float
    tick_value: float
    base_price: float
    min_buffer_points: float   # S1 buffer (e.g. 0.80 for XAUUSD, 0.0002 for EURUSD)
    be_buffer_points: float    # Breakeven buffer (e.g. 0.30 for XAUUSD, 0.0001 for EURUSD)
    sr_proximity_points: float # Proximity margin to consider S/R test
    default_t1_points: float   # Fallback T1 distance
    default_t2_points: float   # Fallback T2 distance
    slippage_tolerance_pips: float
    min_sl_points: float = 2.50       # Sàn dừng lỗ tối thiểu an toàn để tránh bị quét bởi spread
    max_spread_points: float = 0.45   # Ngưỡng trần spread tối đa cho phép vào lệnh
    min_profit_points: float = 2.00   # Khoảng cách giá tối thiểu để đạt mục tiêu lợi nhuận $2.00 trên 0.01 lot
    candle_sl_multiplier: float = 1.20  # Hệ số nhân biên độ nến gần nhất để tính sàn khoảng cách SL động (e.g. 1.2x biên độ nến)
    candle_buffer_ratio: float = 0.25   # Tỷ lệ đệm vượt ngoài đáy/đỉnh nến gần nhất (25% biên độ nến)

def get_instrument_profile(symbol: str) -> InstrumentProfile:
    sym = symbol.upper()
    if "XAU" in sym or "GOLD" in sym:
        return InstrumentProfile(
            symbol=sym,
            digits=2,
            point=0.01,
            contract_size=100.0,
            tick_value=1.0,
            base_price=2650.00,
            min_buffer_points=0.80,    # $0.80 buffer for Gold volatility
            be_buffer_points=2.00,     # $2.00 breakeven cushion to lock at least $2.00 profit on 0.01 lot
            sr_proximity_points=1.50,  # $1.50 zone proximity
            default_t1_points=5.00,    # $5.00 target 1
            default_t2_points=15.00,   # $15.00 target 2
            slippage_tolerance_pips=0.50,
            min_sl_points=2.50,        # Minimum 2.50 USD SL floor on Gold
            max_spread_points=0.45,    # Maximum 0.45 USD spread allowed
            min_profit_points=2.00,    # Minimum $2.00 price move on 0.01 lot = $2.00 USD
            candle_sl_multiplier=1.20, # SL floor = 120% of recent candle range (prevents stop hunts)
            candle_buffer_ratio=0.25   # Extra buffer = 25% of recent candle range beyond wick extremes
        )
    elif "BTC" in sym:
        return InstrumentProfile(
            symbol=sym,
            digits=2,
            point=0.01,
            contract_size=1.0,
            tick_value=0.01,
            base_price=65000.00,
            min_buffer_points=50.00,
            be_buffer_points=25.00,
            sr_proximity_points=100.00,
            default_t1_points=300.00,
            default_t2_points=800.00,
            slippage_tolerance_pips=10.0,
            min_sl_points=150.00,
            max_spread_points=25.00,
            min_profit_points=100.00
        )
    else:
        # Default Forex 5-digit (EURUSD, GBPUSD, etc.)
        return InstrumentProfile(
            symbol=sym,
            digits=5,
            point=0.00001,
            contract_size=100000.0,
            tick_value=1.0,
            base_price=1.08500,
            min_buffer_points=0.00020, # 2 pips
            be_buffer_points=0.00020,  # 2 pips
            sr_proximity_points=0.00050, # 5 pips
            default_t1_points=0.00200, # 20 pips
            default_t2_points=0.00500, # 50 pips
            slippage_tolerance_pips=1.0,
            min_sl_points=0.00150,     # 15 pips min SL
            max_spread_points=0.00030, # 3 pips max spread
            min_profit_points=0.00020  # 2 pips minimum profit
        )

def calculate_pnl(
    entry_price: float,
    close_price: float,
    lot_size: float,
    side: OrderSide,
    profile: Optional[InstrumentProfile] = None
) -> float:
    """
    Calculates realized PnL in USD based on entry, exit, volume, and instrument profile.
    """
    if lot_size <= 0:
        return 0.0
    if side == OrderSide.BUY:
        pts = close_price - entry_price
    else:
        pts = entry_price - close_price

    if profile is None:
        return round(pts * 100.0 * lot_size, 2)

    pnl = pts * profile.contract_size * lot_size
    return round(pnl, 2)

def session_config_to_dict(config: Optional[SessionConfig]) -> Dict[str, Any]:
    if config is None:
        return {}
    return {
        "session_id": config.session_id,
        "symbol": config.symbol,
        "generated_at": config.generated_at,
        "market_regime": config.market_regime.value if hasattr(config.market_regime, "value") else str(config.market_regime),
        "setups_enabled": config.setups_enabled,
        "execution_rules": config.execution_rules,
        "risk_management": config.risk_management,
        "news_filter": config.news_filter,
        "session_tag": config.session_tag,
        "htf_zones": {
            "resistance_zones": [
                {
                    "id": z.id,
                    "high": z.high,
                    "low": z.low,
                    "significance": z.significance.value if hasattr(z.significance, "value") else str(z.significance),
                    "zone_type": getattr(z, "zone_type", "RESISTANCE")
                }
                for z in (config.resistance_zones or [])
            ],
            "support_zones": [
                {
                    "id": z.id,
                    "high": z.high,
                    "low": z.low,
                    "significance": z.significance.value if hasattr(z.significance, "value") else str(z.significance),
                    "zone_type": getattr(z, "zone_type", "SUPPORT")
                }
                for z in (config.support_zones or [])
            ]
        }
    }


