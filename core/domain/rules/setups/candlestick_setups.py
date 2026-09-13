"""
Concrete Price Action Setups (Vol 5):
TrendBarFailSetup, InsideBarSMA21Setup, IDNR4Setup, NR7EMA20Setup, YumYumSetup
"""
from typing import Optional, List, Tuple
from core.domain.models import Bar, SwingNode, HTFZone, SetupType, OrderSide, InstrumentProfile
from core.domain.rules.setups.base import BaseSetup
from core.domain.rules.candlestick_engine import CandlestickEngine
from core.domain.rules.vector_dynamics import MicroPatternDetector

def _calc_thresholds(bars_1m: List[Bar], profile: Optional[InstrumentProfile] = None) -> Tuple[float, float, float]:
    atr = MicroPatternDetector.calculate_atr(bars_1m, period=14)
    if profile:
        prox = max(profile.sr_proximity_points, atr * 0.3)
        t1_dist = max(profile.default_t1_points, atr * 1.5)
        t2_dist = max(profile.default_t2_points, atr * 3.5)
    else:
        last_price = bars_1m[-1].close if bars_1m else 1.0
        prox = max(last_price * 0.0005, atr * 0.3)
        t1_dist = max(last_price * 0.0020, atr * 1.5)
        t2_dist = max(last_price * 0.0040, atr * 3.5)
    return prox, t1_dist, t2_dist


class TrendBarFailSetup(BaseSetup):
    """Module 1: Trend Bar Failure Engine (Counter-trend Trap / Continuation)"""
    def __init__(self):
        super().__init__(SetupType.TREND_BAR_FAIL)

    def evaluate(
        self,
        trend: str,
        swings_3m: List[SwingNode],
        bars_1m: List[Bar],
        resistance_zones: List[HTFZone],
        support_zones: List[HTFZone],
        profile: Optional[InstrumentProfile] = None
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], Optional[float]]:
        if len(bars_1m) < 22:
            return False, None, None, None, None

        tick_size = profile.point if profile else 0.01
        triggered, side, entry_p, sl_p, _ = CandlestickEngine.evaluate_trend_bar_failure(bars_1m, tick_size=tick_size)
        if not triggered or not side or not entry_p:
            return False, None, None, None, None

        _, t1_dist, t2_dist = _calc_thresholds(bars_1m, profile)
        if side == OrderSide.BUY:
            t1 = round(entry_p + t1_dist, profile.digits if profile else 2)
            t2 = round(entry_p + t2_dist, profile.digits if profile else 2)
        else:
            t1 = round(entry_p - t1_dist, profile.digits if profile else 2)
            t2 = round(entry_p - t2_dist, profile.digits if profile else 2)

        return True, side, entry_p, t1, t2


class InsideBarSMA21Setup(BaseSetup):
    """Module 2: Inside Bar Day Trading (SMA 21 First Pullback)"""
    def __init__(self):
        super().__init__(SetupType.INSIDE_BAR_SMA21)

    def evaluate(
        self,
        trend: str,
        swings_3m: List[SwingNode],
        bars_1m: List[Bar],
        resistance_zones: List[HTFZone],
        support_zones: List[HTFZone],
        profile: Optional[InstrumentProfile] = None
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], Optional[float]]:
        if len(bars_1m) < 25:
            return False, None, None, None, None

        tick_size = profile.point if profile else 0.01
        triggered, side, entry_p, sl_p, _ = CandlestickEngine.evaluate_inside_bar_sma21(bars_1m, tick_size=tick_size)
        if not triggered or not side or not entry_p:
            return False, None, None, None, None

        _, t1_dist, t2_dist = _calc_thresholds(bars_1m, profile)
        if side == OrderSide.BUY:
            t1 = round(entry_p + t1_dist, profile.digits if profile else 2)
            t2 = round(entry_p + t2_dist, profile.digits if profile else 2)
        else:
            t1 = round(entry_p - t1_dist, profile.digits if profile else 2)
            t2 = round(entry_p - t2_dist, profile.digits if profile else 2)

        return True, side, entry_p, t1, t2


class IDNR4Setup(BaseSetup):
    """Module 3: Inside Bar NR4 (ID/NR4 Volatility Squeeze at Key Levels)"""
    def __init__(self):
        super().__init__(SetupType.ID_NR4)

    def evaluate(
        self,
        trend: str,
        swings_3m: List[SwingNode],
        bars_1m: List[Bar],
        resistance_zones: List[HTFZone],
        support_zones: List[HTFZone],
        profile: Optional[InstrumentProfile] = None
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], Optional[float]]:
        if len(bars_1m) < 5:
            return False, None, None, None, None

        curr_p = bars_1m[-1].close
        prox, t1_dist, t2_dist = _calc_thresholds(bars_1m, profile)

        is_sup = any(z.contains(curr_p) or abs(curr_p - z.high) <= prox for z in support_zones)
        is_res = any(z.contains(curr_p) or abs(curr_p - z.low) <= prox for z in resistance_zones)

        tick_size = profile.point if profile else 0.01
        triggered, side, entry_p, sl_p, _ = CandlestickEngine.evaluate_id_nr4(
            bars_1m, is_support=is_sup, is_resistance=is_res, trend=trend, tick_size=tick_size
        )
        if not triggered or not side or not entry_p:
            return False, None, None, None, None

        if side == OrderSide.BUY:
            t1 = round(entry_p + t1_dist, profile.digits if profile else 2)
            t2 = round(entry_p + t2_dist, profile.digits if profile else 2)
        else:
            t1 = round(entry_p - t1_dist, profile.digits if profile else 2)
            t2 = round(entry_p - t2_dist, profile.digits if profile else 2)

        return True, side, entry_p, t1, t2


class NR7EMA20Setup(BaseSetup):
    """Module 4: Narrow Range 7 (NR7 System + EMA 20 Isolation)"""
    def __init__(self):
        super().__init__(SetupType.NR7_EMA20)

    def evaluate(
        self,
        trend: str,
        swings_3m: List[SwingNode],
        bars_1m: List[Bar],
        resistance_zones: List[HTFZone],
        support_zones: List[HTFZone],
        profile: Optional[InstrumentProfile] = None
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], Optional[float]]:
        if len(bars_1m) < 28:
            return False, None, None, None, None

        tick_size = profile.point if profile else 0.01
        triggered, side, entry_p, sl_p, _ = CandlestickEngine.evaluate_nr7_ema20(bars_1m, tick_size=tick_size)
        if not triggered or not side or not entry_p:
            return False, None, None, None, None

        _, t1_dist, t2_dist = _calc_thresholds(bars_1m, profile)
        if side == OrderSide.BUY:
            t1 = round(entry_p + t1_dist, profile.digits if profile else 2)
            t2 = round(entry_p + t2_dist, profile.digits if profile else 2)
        else:
            t1 = round(entry_p - t1_dist, profile.digits if profile else 2)
            t2 = round(entry_p - t2_dist, profile.digits if profile else 2)

        return True, side, entry_p, t1, t2


class YumYumSetup(BaseSetup):
    """Module 5: Yum-Yum Continuation Breakout (Wide Range Expansion)"""
    def __init__(self):
        super().__init__(SetupType.YUM_YUM)

    def evaluate(
        self,
        trend: str,
        swings_3m: List[SwingNode],
        bars_1m: List[Bar],
        resistance_zones: List[HTFZone],
        support_zones: List[HTFZone],
        profile: Optional[InstrumentProfile] = None
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], Optional[float]]:
        if len(bars_1m) < 25:
            return False, None, None, None, None

        tick_size = profile.point if profile else 0.01
        triggered, side, entry_p, sl_p, _ = CandlestickEngine.evaluate_yum_yum(bars_1m, tick_size=tick_size)
        if not triggered or not side or not entry_p:
            return False, None, None, None, None

        _, t1_dist, t2_dist = _calc_thresholds(bars_1m, profile)
        if side == OrderSide.BUY:
            t1 = round(entry_p + t1_dist, profile.digits if profile else 2)
            t2 = round(entry_p + t2_dist, profile.digits if profile else 2)
        else:
            t1 = round(entry_p - t1_dist, profile.digits if profile else 2)
            t2 = round(entry_p - t2_dist, profile.digits if profile else 2)

        return True, side, entry_p, t1, t2