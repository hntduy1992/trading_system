"""
Concrete YTC Setup Implementations: TST, BOF, BPB, PB, CPB
Optimized with dynamic instrument thresholds (XAUUSD, Forex, Crypto)
"""
from typing import Optional, List, Tuple
from core.domain.models import Bar, SwingNode, HTFZone, SetupType, OrderSide, SwingType, InstrumentProfile
from core.domain.rules.setups.base import BaseSetup
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

def _is_tst_reaction(bars_1m: List[Bar], side: OrderSide, atr: float) -> bool:
    """
    Verifies that market is rejecting the S/R zone with price action (Wick or Directional Close)
    rather than flatlining or aggressively piercing through without absorption.
    """
    if not bars_1m:
        return False
    curr_bar = bars_1m[-1]
    bar_range = curr_bar.high - curr_bar.low

    if side == OrderSide.BUY:
        # Rejection from Support:
        # 1. Lower wick >= 28% of candle range (buyers absorbing sellers)
        lower_wick = min(curr_bar.open, curr_bar.close) - curr_bar.low
        if bar_range > 0 and (lower_wick / bar_range) >= 0.28:
            return True
        # 2. Bullish candle close above open
        if curr_bar.close > curr_bar.open:
            if len(bars_1m) < 2 or curr_bar.close >= bars_1m[-2].close:
                return True
        # 3. Check prior bar if current bar is consolidating at zone high
        if len(bars_1m) >= 2:
            prev_bar = bars_1m[-2]
            prev_range = prev_bar.high - prev_bar.low
            prev_lower_wick = min(prev_bar.open, prev_bar.close) - prev_bar.low
            if prev_range > 0 and (prev_lower_wick / prev_range) >= 0.35 and curr_bar.close >= prev_bar.low:
                return True
        return False
    else:
        # Rejection from Resistance:
        # 1. Upper wick >= 28% of candle range (sellers absorbing buyers)
        upper_wick = curr_bar.high - max(curr_bar.open, curr_bar.close)
        if bar_range > 0 and (upper_wick / bar_range) >= 0.28:
            return True
        # 2. Bearish candle close below open
        if curr_bar.close < curr_bar.open:
            if len(bars_1m) < 2 or curr_bar.close <= bars_1m[-2].close:
                return True
        # 3. Check prior bar
        if len(bars_1m) >= 2:
            prev_bar = bars_1m[-2]
            prev_range = prev_bar.high - prev_bar.low
            prev_upper_wick = prev_bar.high - max(prev_bar.open, prev_bar.close)
            if prev_range > 0 and (prev_upper_wick / prev_range) >= 0.35 and curr_bar.close <= prev_bar.high:
                return True
        return False

def _is_post_expansion_exhausted(bars_1m: List[Bar], side: OrderSide, atr: float) -> bool:
    """
    Prevents blind counter-trend fading if the market just arrived at the zone via
    a massive momentum expansion bar without any sign of stalling/exhaustion.
    """
    if len(bars_1m) < 2 or atr <= 0:
        return False
    prev_bar = bars_1m[-2]
    prev_range = prev_bar.high - prev_bar.low
    if prev_range >= 2.2 * atr:
        if side == OrderSide.SELL and prev_bar.close > prev_bar.open:
            upper_wick = prev_bar.high - prev_bar.close
            if prev_range > 0 and (upper_wick / prev_range) < 0.15:
                return True
        elif side == OrderSide.BUY and prev_bar.close < prev_bar.open:
            lower_wick = prev_bar.close - prev_bar.low
            if prev_range > 0 and (lower_wick / prev_range) < 0.15:
                return True
    return False

class TSTSetup(BaseSetup):
    """Test of Support/Resistance (Range Trading)"""
    def __init__(self):
        super().__init__(SetupType.TST)

    def evaluate(
        self,
        trend: str,
        swings_3m: List[SwingNode],
        bars_1m: List[Bar],
        resistance_zones: List[HTFZone],
        support_zones: List[HTFZone],
        profile: Optional[InstrumentProfile] = None
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], Optional[float]]:
        if not bars_1m or not swings_3m:
            return False, None, None, None, None

        curr_bar = bars_1m[-1]
        curr_price = curr_bar.close
        prox, t1_dist, t2_dist = _calc_thresholds(bars_1m, profile)
        atr = MicroPatternDetector.calculate_atr(bars_1m, period=14)

        # Check Test of Support -> BUY
        for sup in support_zones:
            if sup.contains(curr_price) or abs(curr_price - sup.high) <= prox:
                if _is_post_expansion_exhausted(bars_1m, OrderSide.BUY, atr):
                    continue
                if not _is_tst_reaction(bars_1m, OrderSide.BUY, atr):
                    continue
                recent_sh = [s for s in swings_3m if s.swing_type == SwingType.SWING_HIGH]
                t1 = recent_sh[-1].price if recent_sh else (curr_price + t1_dist)
                t2 = resistance_zones[0].low if resistance_zones else (curr_price + t2_dist)
                pullback_swing = sup.low
                return True, OrderSide.BUY, pullback_swing, t1, t2

        # Check Test of Resistance -> SELL
        for res in resistance_zones:
            if res.contains(curr_price) or abs(curr_price - res.low) <= prox:
                if _is_post_expansion_exhausted(bars_1m, OrderSide.SELL, atr):
                    continue
                if not _is_tst_reaction(bars_1m, OrderSide.SELL, atr):
                    continue
                recent_sl = [s for s in swings_3m if s.swing_type == SwingType.SWING_LOW]
                t1 = recent_sl[-1].price if recent_sl else (curr_price - t1_dist)
                t2 = support_zones[0].high if support_zones else (curr_price - t2_dist)
                pullback_swing = res.high
                return True, OrderSide.SELL, pullback_swing, t1, t2

        return False, None, None, None, None


class BOFSetup(BaseSetup):
    """Breakout Failure (False Breakout / Trap)"""
    def __init__(self):
        super().__init__(SetupType.BOF)

    def evaluate(
        self,
        trend: str,
        swings_3m: List[SwingNode],
        bars_1m: List[Bar],
        resistance_zones: List[HTFZone],
        support_zones: List[HTFZone],
        profile: Optional[InstrumentProfile] = None
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], Optional[float]]:
        if not bars_1m or not swings_3m:
            return False, None, None, None, None

        curr_bar = bars_1m[-1]
        _, t1_dist, t2_dist = _calc_thresholds(bars_1m, profile)

        # Check Spring at Support -> BUY
        for sup in support_zones:
            if MicroPatternDetector.detect_spring(curr_bar, sup.low):
                recent_sh = [s for s in swings_3m if s.swing_type == SwingType.SWING_HIGH]
                t1 = recent_sh[-1].price if recent_sh else (curr_bar.close + t1_dist)
                t2 = resistance_zones[0].low if resistance_zones else (curr_bar.close + t2_dist)
                return True, OrderSide.BUY, curr_bar.low, t1, t2

        # Check Upthrust at Resistance -> SELL
        for res in resistance_zones:
            if MicroPatternDetector.detect_upthrust(curr_bar, res.high):
                recent_sl = [s for s in swings_3m if s.swing_type == SwingType.SWING_LOW]
                t1 = recent_sl[-1].price if recent_sl else (curr_bar.close - t1_dist)
                t2 = support_zones[0].high if support_zones else (curr_bar.close - t2_dist)
                return True, OrderSide.SELL, curr_bar.high, t1, t2

        return False, None, None, None, None


class BPBSetup(BaseSetup):
    """Breakout Pullback (Post-expansion continuation)"""
    def __init__(self):
        super().__init__(SetupType.BPB)

    def evaluate(
        self,
        trend: str,
        swings_3m: List[SwingNode],
        bars_1m: List[Bar],
        resistance_zones: List[HTFZone],
        support_zones: List[HTFZone],
        profile: Optional[InstrumentProfile] = None
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], Optional[float]]:
        if not bars_1m or len(swings_3m) < 2:
            return False, None, None, None, None

        curr_price = bars_1m[-1].close
        prox, t1_dist, t2_dist = _calc_thresholds(bars_1m, profile)

        # Broken Resistance turned support (Long BPB)
        for res in resistance_zones:
            if curr_price > res.high:
                pullback_low = min(b.low for b in bars_1m[-5:])
                if abs(pullback_low - res.high) <= prox:
                    t1 = curr_price + t1_dist
                    t2 = curr_price + t2_dist
                    return True, OrderSide.BUY, pullback_low, t1, t2

        # Broken Support turned resistance (Short BPB)
        for sup in support_zones:
            if curr_price < sup.low:
                pullback_high = max(b.high for b in bars_1m[-5:])
                if abs(pullback_high - sup.low) <= prox:
                    t1 = curr_price - t1_dist
                    t2 = curr_price - t2_dist
                    return True, OrderSide.SELL, pullback_high, t1, t2

        return False, None, None, None, None


class PBSetup(BaseSetup):
    """Simple Pullback (Trend Continuation)"""
    def __init__(self):
        super().__init__(SetupType.PB)

    def evaluate(
        self,
        trend: str,
        swings_3m: List[SwingNode],
        bars_1m: List[Bar],
        resistance_zones: List[HTFZone],
        support_zones: List[HTFZone],
        profile: Optional[InstrumentProfile] = None
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], Optional[float]]:
        if not bars_1m or len(swings_3m) < 3:
            return False, None, None, None, None

        curr_price = bars_1m[-1].close
        last_swing = swings_3m[-1]
        _, t1_dist, t2_dist = _calc_thresholds(bars_1m, profile)

        if "UPTREND" in trend and last_swing.swing_type == SwingType.SWING_LOW:
            recent_sh = [s for s in swings_3m if s.swing_type == SwingType.SWING_HIGH]
            t1 = recent_sh[-1].price
            t2 = resistance_zones[0].low if resistance_zones else (t1 + t2_dist)
            return True, OrderSide.BUY, last_swing.price, t1, t2

        if "DOWNTREND" in trend and last_swing.swing_type == SwingType.SWING_HIGH:
            recent_sl = [s for s in swings_3m if s.swing_type == SwingType.SWING_LOW]
            t1 = recent_sl[-1].price
            t2 = support_zones[0].high if support_zones else (t1 - t2_dist)
            return True, OrderSide.SELL, last_swing.price, t1, t2

        return False, None, None, None, None


class CPBSetup(BaseSetup):
    """Complex Pullback (Two-legged / ABC Pullback)"""
    def __init__(self):
        super().__init__(SetupType.CPB)

    def evaluate(
        self,
        trend: str,
        swings_3m: List[SwingNode],
        bars_1m: List[Bar],
        resistance_zones: List[HTFZone],
        support_zones: List[HTFZone],
        profile: Optional[InstrumentProfile] = None
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], Optional[float]]:
        if not bars_1m or len(swings_3m) < 5:
            return False, None, None, None, None

        curr_price = bars_1m[-1].close
        last_swing = swings_3m[-1]
        prior_swing = swings_3m[-3]
        _, t1_dist, t2_dist = _calc_thresholds(bars_1m, profile)

        if "UPTREND" in trend and last_swing.swing_type == SwingType.SWING_LOW:
            recent_sh = [s for s in swings_3m if s.swing_type == SwingType.SWING_HIGH]
            t1 = recent_sh[-1].price
            t2 = resistance_zones[0].low if resistance_zones else (t1 + t2_dist)
            return True, OrderSide.BUY, min(last_swing.price, prior_swing.price), t1, t2

        if "DOWNTREND" in trend and last_swing.swing_type == SwingType.SWING_HIGH:
            recent_sl = [s for s in swings_3m if s.swing_type == SwingType.SWING_LOW]
            t1 = recent_sl[-1].price
            t2 = support_zones[0].high if support_zones else (t1 - t2_dist)
            return True, OrderSide.SELL, max(last_swing.price, prior_swing.price), t1, t2

        return False, None, None, None, None
