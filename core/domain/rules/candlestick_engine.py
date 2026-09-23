"""
=============================================================================
CANDLESTICK ENGINE: 5 CORE PRICE ACTION STRATEGIES (VOL 5)
Conforming strictly to Algorithmic Specification & Pseudocode (Vol 5)
=============================================================================
Module 1: Trend Bar Failure Engine (Counter-trend Trap / Continuation)
Module 2: Inside Bar Day Trading (SMA 21 First Pullback + Anti-Congestion Filter)
Module 3: Inside Bar NR4 (ID/NR4 Volatility Squeeze)
Module 4: Narrow Range 7 (NR7 System + EMA 20 Isolation)
Module 5: Yum-Yum Continuation Breakout (Wide Range Expansion)
"""
from typing import List, Tuple, Optional, Dict, Any
from core.domain.models import Bar, OrderSide, TradeLifecycle

class CandlestickEngine:
    """
    Algorithmic Price Action Primitive Evaluator.
    Computes mathematical predicates, geometric candle properties,
    and execution triggers.
    """

    # -------------------------------------------------------------------------
    # 1. MATHEMATICAL PRIMITIVES & INDICATORS
    # -------------------------------------------------------------------------

    @staticmethod
    def detect_trend_bar(bar: Bar) -> Tuple[bool, str]:
        """
        Detects if a candle qualifies as a Trend Bar (body > 50% of full range).
        Returns: (is_trend_bar, "BULLISH" | "BEARISH" | "NEUTRAL")
        """
        if bar.range == 0.0:
            return False, "NEUTRAL"

        body_ratio = bar.body / bar.range
        if body_ratio > 0.50:
            if bar.close > bar.open:
                return True, "BULLISH"
            else:
                return True, "BEARISH"
        return False, "NEUTRAL"

    @staticmethod
    def is_inside_bar(child: Bar, parent: Bar) -> bool:
        """
        Returns True if child bar is completely contained within parent bar boundaries.
        child.high <= parent.high AND child.low >= parent.low
        """
        return (child.high <= parent.high) and (child.low >= parent.low)

    @staticmethod
    def is_congestion(bars: List[Bar], lookback: int = 5, min_symmetric_bars: int = 3) -> bool:
        """
        Identifies choppy, sideways, indecisive market with excessive overlapping long wicks.
        Returns True if >= min_symmetric_bars in lookback have both upper & lower wicks >= 30% of range.
        """
        if len(bars) < lookback:
            return False

        recent_bars = bars[-lookback:]
        symmetric_wick_bars = 0
        for b in recent_bars:
            if b.range > 0.0:
                upper_ratio = b.upper_wick / b.range
                lower_ratio = b.lower_wick / b.range
                if upper_ratio >= 0.30 and lower_ratio >= 0.30:
                    symmetric_wick_bars += 1

        return symmetric_wick_bars >= min_symmetric_bars

    @staticmethod
    def is_nr4(bars: List[Bar], offset: int = 0) -> bool:
        """
        Narrow Range 4 (NR4): Current target bar's range is strictly smaller
        than the preceding 3 bars.
        offset=0 tests the latest closed bar (bars[-1]).
        """
        target_idx = len(bars) - 1 - offset
        if target_idx < 3:
            return False

        target_range = bars[target_idx].range
        for k in range(1, 4):
            if target_range >= bars[target_idx - k].range:
                return False
        return True

    @staticmethod
    def is_id_nr4(bars: List[Bar], offset: int = 0) -> bool:
        """
        Inside Bar NR4 (ID/NR4): Combines Inside Bar with NR4 for extreme volatility squeeze.
        """
        target_idx = len(bars) - 1 - offset
        if target_idx < 3:
            return False

        child = bars[target_idx]
        parent = bars[target_idx - 1]
        return CandlestickEngine.is_inside_bar(child, parent) and CandlestickEngine.is_nr4(bars, offset)

    @staticmethod
    def is_nr7(bars: List[Bar], offset: int = 0) -> bool:
        """
        Narrow Range 7 (NR7): Target bar's range is strictly smaller than the preceding 6 bars.
        """
        target_idx = len(bars) - 1 - offset
        if target_idx < 6:
            return False

        target_range = bars[target_idx].range
        for k in range(1, 7):
            if target_range >= bars[target_idx - k].range:
                return False
        return True

    @staticmethod
    def is_wide_range_breakout(bars: List[Bar], offset: int = 0, lookback: int = 10) -> bool:
        """
        Wide Range Breakout: Target bar's range is strictly greater than all previous `lookback` bars.
        """
        target_idx = len(bars) - 1 - offset
        if target_idx < lookback:
            return False

        target_range = bars[target_idx].range
        for k in range(1, lookback + 1):
            if target_range <= bars[target_idx - k].range:
                return False
        return True

    @staticmethod
    def calc_ema(closes: List[float], period: int) -> List[float]:
        """
        Calculates Exponential Moving Average across a series of closing prices.
        """
        if not closes or len(closes) < period:
            return [closes[-1]] * len(closes) if closes else []

        multiplier = 2.0 / (period + 1.0)
        ema = [sum(closes[:period]) / period]

        for price in closes[period:]:
            new_ema = (price - ema[-1]) * multiplier + ema[-1]
            ema.append(new_ema)

        # Pad initial elements to align with input length
        pad_count = len(closes) - len(ema)
        return [ema[0]] * pad_count + ema

    @staticmethod
    def calc_sma(closes: List[float], period: int) -> List[float]:
        """
        Calculates Simple Moving Average across a series of closing prices.
        """
        if not closes or len(closes) < period:
            return [closes[-1]] * len(closes) if closes else []

        sma = []
        for i in range(len(closes)):
            if i < period - 1:
                sma.append(sum(closes[:i+1]) / (i + 1))
            else:
                sma.append(sum(closes[i-period+1:i+1]) / period)
        return sma

    # -------------------------------------------------------------------------
    # 2. MODULE EVALUATORS (EXECUTION PROTOCOLS)
    # -------------------------------------------------------------------------

    @staticmethod
    def evaluate_trend_bar_failure(
        bars: List[Bar],
        tick_size: float = 0.01
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], int]:
        """
        MODULE 1: Trend Bar Failure Engine (Counter-trend Trap)
        - Precondition Bar[2]: Strong Trend Bar (Bullish or Bearish)
        - Trigger Bar[1]: Exceeds Bar[2] extreme but FAILS to continue trend bar momentum (Trap!)
        - Market context: EMA(20) alignment
        - Auto-cancel: Strict 1-Bar Expiry (max_bars=1)
        Returns: (triggered, side, entry_price, sl_price, max_bars)
        """
        if len(bars) < 22:
            return False, None, None, None, 1

        closes = [b.close for b in bars]
        ema20 = CandlestickEngine.calc_ema(closes, period=20)

        bar1 = bars[-1] # Trigger bar
        bar2 = bars[-2] # Precondition bar

        is_tb_bar2, dir_bar2 = CandlestickEngine.detect_trend_bar(bar2)
        is_tb_bar1, dir_bar1 = CandlestickEngine.detect_trend_bar(bar1)

        # 1. BUY SIGNAL: Bearish Trend Bar Failure (Trapped Bears)
        # Context: EMA20 rising OR Close[1] > EMA20
        context_bull = (ema20[-1] > ema20[-2]) or (bar1.close > ema20[-1])
        if context_bull and is_tb_bar2 and dir_bar2 == "BEARISH":
            # Bar 1 makes a lower low than Bar 2, but FAILS to be a Bearish Trend Bar
            if bar1.low < bar2.low and not (is_tb_bar1 and dir_bar1 == "BEARISH"):
                # SL dựa vào biên độ nến gần nhất: đệm ít nhất 25% biên độ nến, tối thiểu 20 tick
                bar1_range = bar1.high - bar1.low
                sl_buffer = max(tick_size * 20, bar1_range * 0.25)
                entry_price = bar1.high + tick_size
                sl_price = bar1.low - sl_buffer
                return True, OrderSide.BUY, entry_price, sl_price, 1

        # 2. SELL SIGNAL: Bullish Trend Bar Failure (Trapped Bulls)
        # Context: EMA20 falling OR Close[1] < EMA20
        context_bear = (ema20[-1] < ema20[-2]) or (bar1.close < ema20[-1])
        if context_bear and is_tb_bar2 and dir_bar2 == "BULLISH":
            # Bar 1 makes a higher high than Bar 2, but FAILS to be a Bullish Trend Bar
            if bar1.high > bar2.high and not (is_tb_bar1 and dir_bar1 == "BULLISH"):
                # SL dựa vào biên độ nến gần nhất: đệm ít nhất 25% biên độ nến, tối thiểu 20 tick
                bar1_range = bar1.high - bar1.low
                sl_buffer = max(tick_size * 20, bar1_range * 0.25)
                entry_price = bar1.low - tick_size
                sl_price = bar1.high + sl_buffer
                return True, OrderSide.SELL, entry_price, sl_price, 1

        return False, None, None, None, 1

    @staticmethod
    def evaluate_inside_bar_sma21(
        bars: List[Bar],
        tick_size: float = 0.01
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], int]:
        """
        MODULE 2: Inside Bar Day Trading (SMA 21 First Pullback)
        - Noise filter: NOT IsCongestion(5)
        - Structure: Bar[1] is Inside Bar of Bar[2]
        - First Pullback after structural SMA21 break
        Returns: (triggered, side, entry_price, sl_price, max_bars)
        """
        if len(bars) < 25:
            return False, None, None, None, 3

        # Congestion filter: reject choppy markets
        if CandlestickEngine.is_congestion(bars, lookback=5):
            return False, None, None, None, 3

        child = bars[-1]
        mother = bars[-2]

        if not CandlestickEngine.is_inside_bar(child, mother):
            return False, None, None, None, 3

        closes = [b.close for b in bars]
        sma21 = CandlestickEngine.calc_sma(closes, period=21)

        # Bullish setup: Price broke above SMA21 and mother bar holds above SMA21
        if mother.low > sma21[-2] and closes[-3] > sma21[-3]:
            mother_range = mother.high - mother.low
            sl_buffer = max(tick_size * 20, mother_range * 0.25)
            entry_price = mother.high + tick_size
            sl_price = mother.low - sl_buffer
            return True, OrderSide.BUY, entry_price, sl_price, 3

        # Bearish setup: Price broke below SMA21 and mother bar holds below SMA21
        if mother.high < sma21[-2] and closes[-3] < sma21[-3]:
            mother_range = mother.high - mother.low
            sl_buffer = max(tick_size * 20, mother_range * 0.25)
            entry_price = mother.low - tick_size
            sl_price = mother.high + sl_buffer
            return True, OrderSide.SELL, entry_price, sl_price, 3

        return False, None, None, None, 3

    @staticmethod
    def evaluate_id_nr4(
        bars: List[Bar],
        is_support: bool = False,
        is_resistance: bool = False,
        trend: str = "UP",
        tick_size: float = 0.01
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], int]:
        """
        MODULE 3: Inside Bar NR4 (ID/NR4 Squeeze at Key Levels)
        - Pattern: Inside Bar + NR4 on Bar[1]
        - Context: At Support in UPTREND -> BUY; At Resistance in DOWNTREND -> SELL
        Returns: (triggered, side, entry_price, sl_price, max_bars)
        """
        if len(bars) < 5:
            return False, None, None, None, 2

        if not CandlestickEngine.is_id_nr4(bars, offset=0):
            return False, None, None, None, 2

        bar1 = bars[-1]

        if is_support and "UP" in trend.upper():
            bar1_range = bar1.high - bar1.low
            sl_buffer = max(tick_size * 20, bar1_range * 0.25)
            entry_price = bar1.high + tick_size
            sl_price = bar1.low - sl_buffer
            return True, OrderSide.BUY, entry_price, sl_price, 2

        if is_resistance and "DOWN" in trend.upper():
            bar1_range = bar1.high - bar1.low
            sl_buffer = max(tick_size * 20, bar1_range * 0.25)
            entry_price = bar1.low - tick_size
            sl_price = bar1.high + sl_buffer
            return True, OrderSide.SELL, entry_price, sl_price, 2

        return False, None, None, None, 2

    @staticmethod
    def evaluate_nr7_ema20(
        bars: List[Bar],
        tick_size: float = 0.01
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], int]:
        """
        MODULE 4: Narrow Range 7 (NR7) System (High-Momentum Volatility Breakout)
        - Pattern: IsNR7(Bar[1]) and NOT IsNR7(Bar[2]) (rejects consecutive NR7s)
        - Buy filter: Prior 6 bars fully above EMA 20 (Bar[k].low > EMA 20)
        - Sell filter: Prior 6 bars fully below EMA 20 (Bar[k].high < EMA 20)
        - Entry: Bar[1] boundary +/- 2*tick_size
        Returns: (triggered, side, entry_price, sl_price, max_bars)
        """
        if len(bars) < 28:
            return False, None, None, None, 2

        # Check NR7 at Bar[1] and ensure Bar[2] is not NR7
        if not (CandlestickEngine.is_nr7(bars, offset=0) and not CandlestickEngine.is_nr7(bars, offset=1)):
            return False, None, None, None, 2

        closes = [b.close for b in bars]
        ema20 = CandlestickEngine.calc_ema(closes, period=20)

        # Check BUY FILTER: prior 6 bars fully above EMA 20
        is_bullish_aligned = True
        for k in range(1, 7):
            idx = len(bars) - 1 - k
            if bars[idx].low <= ema20[idx]:
                is_bullish_aligned = False
                break

        bar1 = bars[-1]
        bar1_range = bar1.high - bar1.low
        sl_buffer = max(tick_size * 20, bar1_range * 0.25)

        if is_bullish_aligned:
            entry_price = bar1.high + (2.0 * tick_size)
            sl_price = bar1.low - sl_buffer
            return True, OrderSide.BUY, entry_price, sl_price, 2

        # Check SELL FILTER: prior 6 bars fully below EMA 20
        is_bearish_aligned = True
        for k in range(1, 7):
            idx = len(bars) - 1 - k
            if bars[idx].high >= ema20[idx]:
                is_bearish_aligned = False
                break

        if is_bearish_aligned:
            entry_price = bar1.low - (2.0 * tick_size)
            sl_price = bar1.high + sl_buffer
            return True, OrderSide.SELL, entry_price, sl_price, 2

        return False, None, None, None, 2

    @staticmethod
    def evaluate_yum_yum(
        bars: List[Bar],
        tick_size: float = 0.01
    ) -> Tuple[bool, Optional[OrderSide], Optional[float], Optional[float], int]:
        """
        MODULE 5: Yum-Yum Continuation Breakout (Wide Range Expansion)
        - Expansion: IsWideRangeBreakout(10)
        - Buy: Close > EMA21 and EMA21 rising, Close in top 20% of range
        - Sell: Close < EMA21 and EMA21 falling, Close in bottom 20% of range
        - Auto-cancel: max_bars=3
        Returns: (triggered, side, entry_price, sl_price, max_bars)
        """
        if len(bars) < 25:
            return False, None, None, None, 3

        bar1 = bars[-1]
        if bar1.range <= 0.0:
            return False, None, None, None, 3

        if not CandlestickEngine.is_wide_range_breakout(bars, offset=0, lookback=10):
            return False, None, None, None, 3

        closes = [b.close for b in bars]
        ema21 = CandlestickEngine.calc_ema(closes, period=21)

        # BUY LOGIC: Close in top 20% of bar range
        top_20_pct = (bar1.high - bar1.close) / bar1.range <= 0.20
        if top_20_pct and (bar1.close > ema21[-1]) and (ema21[-1] > ema21[-2]):
            recent_lows = [b.low for b in bars[-6:]]
            bar1_range = bar1.high - bar1.low
            sl_buffer = max(tick_size * 20, bar1_range * 0.25)
            sl_price = min(recent_lows) - sl_buffer
            entry_price = bar1.high + tick_size
            return True, OrderSide.BUY, entry_price, sl_price, 3

        # SELL LOGIC: Close in bottom 20% of bar range
        bottom_20_pct = (bar1.close - bar1.low) / bar1.range <= 0.20
        if bottom_20_pct and (bar1.close < ema21[-1]) and (ema21[-1] < ema21[-2]):
            recent_highs = [b.high for b in bars[-6:]]
            bar1_range = bar1.high - bar1.low
            sl_buffer = max(tick_size * 20, bar1_range * 0.25)
            sl_price = max(recent_highs) + sl_buffer
            entry_price = bar1.low - tick_size
            return True, OrderSide.SELL, entry_price, sl_price, 3

        return False, None, None, None, 3

    # -------------------------------------------------------------------------
    # 3. CONTEXT EXTRACTOR FOR AI GATEKEEPER
    # -------------------------------------------------------------------------

    @staticmethod
    def extract_micro_candle_context(bars: List[Bar]) -> Dict[str, Any]:
        """
        Builds a comprehensive dictionary of micro candlestick characteristics
        to inform AI pre-entry evaluation and risk gating.
        """
        if not bars:
            return {}

        last_bar = bars[-1]
        prev_bar = bars[-2] if len(bars) >= 2 else last_bar

        is_tb, dir_tb = CandlestickEngine.detect_trend_bar(last_bar)
        is_ib = CandlestickEngine.is_inside_bar(last_bar, prev_bar) if len(bars) >= 2 else False
        is_cg = CandlestickEngine.is_congestion(bars, lookback=5)
        is_nr4_val = CandlestickEngine.is_nr4(bars, offset=0)
        is_nr7_val = CandlestickEngine.is_nr7(bars, offset=0)
        is_wr_val = CandlestickEngine.is_wide_range_breakout(bars, offset=0, lookback=10) if len(bars) >= 11 else False

        return {
            "last_bar_range": round(last_bar.range, 3),
            "is_trend_bar": is_tb,
            "trend_bar_direction": dir_tb,
            "is_inside_bar": is_ib,
            "is_congestion": is_cg,
            "is_nr4": is_nr4_val,
            "is_nr7": is_nr7_val,
            "is_wide_range_breakout": is_wr_val,
            "upper_wick_ratio": round(last_bar.upper_wick / last_bar.range, 2) if last_bar.range > 0 else 0.0,
            "lower_wick_ratio": round(last_bar.lower_wick / last_bar.range, 2) if last_bar.range > 0 else 0.0,
        }

    # -------------------------------------------------------------------------
    # 4. CANDLESTICK EXIT ENGINE (PRICE ACTION REVERSAL EXITS)
    # -------------------------------------------------------------------------

    @staticmethod
    def evaluate_candlestick_exit(
        trade: TradeLifecycle,
        bars_m1: List[Bar],
        bars_m3: Optional[List[Bar]] = None,
        min_r: float = 0.0,
        curr_price: Optional[float] = None
    ) -> Tuple[bool, str]:
        """
        Candlestick Exit Engine:
        Evaluates micro Price Action candlestick patterns on closed M1 (and M3) bars
        to trigger active market closure instead of relying on trailing stop loss.

        Patterns Detected:
        1. Pin Bar Rejection (Shooting Star for BUY, Hammer for SELL)
        2. Engulfing Bar Reversal (Bearish Engulfing for BUY, Bullish Engulfing for SELL)
        3. Momentum Reversal Bar (Opposite trend bar with wide expansion >= 1.25 ATR)
        4. Climactic Exhaustion Candle (Wide range >= 2.0 ATR with heavy opposing wick)
        5. Two-Bar Reversal (Opposing candle penetrating deeply > 50% into previous bar)

        Returns:
            (should_exit: bool, exit_reason: str)
        """
        if not bars_m1 or len(bars_m1) < 2:
            return False, ""

        curr_bar = bars_m1[-1]
        prev_bar = bars_m1[-2]

        if curr_bar.range <= 0.0:
            return False, ""

        from core.domain.rules.vector_dynamics import MicroPatternDetector
        atr = MicroPatternDetector.calculate_atr(bars_m1, period=14) if len(bars_m1) >= 5 else 1.0
        if atr <= 0.0:
            atr = 1.0

        current_p = curr_price if curr_price is not None else curr_bar.close

        # Calculate unrealized R progress
        risk_dist = getattr(trade, "initial_risk_dist", 0.0)
        if not risk_dist or risk_dist <= 0:
            risk_dist = abs(trade.part1.entry_price - trade.part1.sl_price)

        if trade.side == OrderSide.BUY:
            profit_dist = current_p - trade.part1.entry_price
        else:
            profit_dist = trade.part1.entry_price - current_p

        unrealized_r = (profit_dist / risk_dist) if risk_dist > 0 else 0.0

        # Don't trigger exits if profit hasn't reached min_r and positive profit
        has_min_profit = (unrealized_r >= min_r) and (profit_dist > 0.0)

        # ---------------------------------------------------------------------
        # 1. EVALUATION FOR BUY (LONG) POSITIONS
        # ---------------------------------------------------------------------
        if trade.side == OrderSide.BUY:
            # A. Shooting Star / Bearish Pin Bar Rejection (Strong selling rejection wick at highs)
            upper_ratio = curr_bar.upper_wick / curr_bar.range
            lower_ratio = curr_bar.lower_wick / curr_bar.range
            body_ratio = curr_bar.body / curr_bar.range
            close_in_bottom = (curr_bar.close - curr_bar.low) / curr_bar.range <= 0.40

            if upper_ratio >= 0.55 and lower_ratio <= 0.30 and close_in_bottom:
                if has_min_profit:
                    return True, f"CANDLE_EXIT_PINBAR_REJECTION (Upper wick {upper_ratio*100:.0f}%, range {curr_bar.range:.2f})"

            # B. Bearish Engulfing Bar (Bearish body engulfs previous bullish bar)
            prev_was_bullish = prev_bar.close >= prev_bar.open
            curr_is_bearish = curr_bar.close < curr_bar.open
            if curr_is_bearish and prev_was_bullish and body_ratio >= 0.50:
                if curr_bar.close < prev_bar.low and curr_bar.open >= (prev_bar.close - 0.10):
                    if has_min_profit:
                        return True, f"CANDLE_EXIT_BEARISH_ENGULFING (Close {curr_bar.close:.2f} < Prev low {prev_bar.low:.2f})"

            # C. Bearish Momentum Reversal (Large opposite trend bar >= 1.25 ATR breaking lows)
            is_tb, dir_tb = CandlestickEngine.detect_trend_bar(curr_bar)
            if is_tb and dir_tb == "BEARISH":
                is_expansion = curr_bar.range >= max(1.25 * atr, 0.6)
                if is_expansion and curr_bar.close < prev_bar.low and has_min_profit:
                    return True, f"CANDLE_EXIT_MOMENTUM_REVERSAL (Range {curr_bar.range:.2f} >= 1.25*ATR {1.25*atr:.2f})"

            # D. Climactic Exhaustion Bar (Ultra-wide expansion with upper wick rejection)
            if curr_bar.range >= max(2.0 * atr, 1.2) and curr_bar.high > prev_bar.high and has_min_profit:
                if upper_ratio >= 0.35 or (curr_bar.high - curr_bar.close) >= 0.45 * curr_bar.range:
                    return True, f"CANDLE_EXIT_CLIMAX_EXHAUSTION (Range {curr_bar.range:.2f} >= 2.0*ATR, upper wick {upper_ratio*100:.0f}%)"

            # E. Two-Bar Reversal / Dark Cloud Cover
            if prev_was_bullish and curr_is_bearish and prev_bar.range > 0:
                prev_midpoint = (prev_bar.open + prev_bar.close) / 2.0
                if curr_bar.close < prev_midpoint and curr_bar.high >= prev_bar.high - 0.20:
                    if has_min_profit and curr_bar.body >= 0.50 * curr_bar.range:
                        return True, f"CANDLE_EXIT_TWO_BAR_REVERSAL (Penetrated below prev midpoint {prev_midpoint:.2f})"

        # ---------------------------------------------------------------------
        # 2. EVALUATION FOR SELL (SHORT) POSITIONS
        # ---------------------------------------------------------------------
        else:
            # A. Hammer / Bullish Pin Bar Rejection (Strong buying rejection wick at lows)
            upper_ratio = curr_bar.upper_wick / curr_bar.range
            lower_ratio = curr_bar.lower_wick / curr_bar.range
            body_ratio = curr_bar.body / curr_bar.range
            close_in_top = (curr_bar.high - curr_bar.close) / curr_bar.range <= 0.40

            if lower_ratio >= 0.55 and upper_ratio <= 0.30 and close_in_top:
                if has_min_profit:
                    return True, f"CANDLE_EXIT_PINBAR_REJECTION (Lower wick {lower_ratio*100:.0f}%, range {curr_bar.range:.2f})"

            # B. Bullish Engulfing Bar (Bullish body engulfs previous bearish bar)
            prev_was_bearish = prev_bar.close <= prev_bar.open
            curr_is_bullish = curr_bar.close > curr_bar.open
            if curr_is_bullish and prev_was_bearish and body_ratio >= 0.50:
                if curr_bar.close > prev_bar.high and curr_bar.open <= (prev_bar.close + 0.10):
                    if has_min_profit:
                        return True, f"CANDLE_EXIT_BULLISH_ENGULFING (Close {curr_bar.close:.2f} > Prev high {prev_bar.high:.2f})"

            # C. Bullish Momentum Reversal (Large opposite trend bar >= 1.25 ATR breaking highs)
            is_tb, dir_tb = CandlestickEngine.detect_trend_bar(curr_bar)
            if is_tb and dir_tb == "BULLISH":
                is_expansion = curr_bar.range >= max(1.25 * atr, 0.6)
                if is_expansion and curr_bar.close > prev_bar.high and has_min_profit:
                    return True, f"CANDLE_EXIT_MOMENTUM_REVERSAL (Range {curr_bar.range:.2f} >= 1.25*ATR {1.25*atr:.2f})"

            # D. Climactic Exhaustion Bar (Ultra-wide expansion down with lower wick rejection)
            if curr_bar.range >= max(2.0 * atr, 1.2) and curr_bar.low < prev_bar.low and has_min_profit:
                if lower_ratio >= 0.35 or (curr_bar.close - curr_bar.low) >= 0.45 * curr_bar.range:
                    return True, f"CANDLE_EXIT_CLIMAX_EXHAUSTION (Range {curr_bar.range:.2f} >= 2.0*ATR, lower wick {lower_ratio*100:.0f}%)"

            # E. Two-Bar Reversal / Piercing Line
            if prev_was_bearish and curr_is_bullish and prev_bar.range > 0:
                prev_midpoint = (prev_bar.open + prev_bar.close) / 2.0
                if curr_bar.close > prev_midpoint and curr_bar.low <= prev_bar.low + 0.20:
                    if has_min_profit and curr_bar.body >= 0.50 * curr_bar.range:
                        return True, f"CANDLE_EXIT_TWO_BAR_REVERSAL (Penetrated above prev midpoint {prev_midpoint:.2f})"

        # ---------------------------------------------------------------------
        # 3. HIGHER TIMEFRAME CONFIRMATION (M3 Pin Bar / Engulfing)
        # ---------------------------------------------------------------------
        if bars_m3 and len(bars_m3) >= 2 and has_min_profit:
            curr_m3 = bars_m3[-1]
            if curr_m3.range > 0:
                if trade.side == OrderSide.BUY:
                    if (curr_m3.upper_wick / curr_m3.range) >= 0.60:
                        return True, f"CANDLE_EXIT_M3_PINBAR (M3 Upper Wick {(curr_m3.upper_wick/curr_m3.range)*100:.0f}%)"
                else:
                    if (curr_m3.lower_wick / curr_m3.range) >= 0.60:
                        return True, f"CANDLE_EXIT_M3_PINBAR (M3 Lower Wick {(curr_m3.lower_wick/curr_m3.range)*100:.0f}%)"

        return False, ""