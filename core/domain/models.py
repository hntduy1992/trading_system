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
    rule_violations: List[Dict[str, Any]]
    hindsight_optimal_trades: List[Dict[str, Any]]
    lessons_learned: List[str]
    parameter_adjustments_suggested: Dict[str, Any]

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
            be_buffer_points=0.30,     # $0.30 breakeven cushion to withstand Gold spread
            sr_proximity_points=1.50,  # $1.50 zone proximity
            default_t1_points=5.00,    # $5.00 target 1
            default_t2_points=15.00,   # $15.00 target 2
            slippage_tolerance_pips=0.50
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
            slippage_tolerance_pips=10.0
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
            be_buffer_points=0.00010,  # 1 pip
            sr_proximity_points=0.00050, # 5 pips
            default_t1_points=0.00200, # 20 pips
            default_t2_points=0.00500, # 50 pips
            slippage_tolerance_pips=1.0
        )

