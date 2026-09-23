"""
Tests: Candle Range-Based Stop Loss
Verifies that SL is placed beyond recent candle extremes (not just 1 tick away),
preventing stop-hunts / liquidity sweeps that stop out trades before they move in the
expected direction.
"""
import unittest
from core.domain.models import Bar, SetupType, OrderSide, get_instrument_profile
from core.domain.rules.wholesale_engine import WholesaleEngine
from core.domain.rules.candlestick_engine import CandlestickEngine


def _make_bar(open_=2600.0, high=2603.0, low=2597.0, close=2602.0, timestamp=None):
    return Bar(
        open=open_, high=high, low=low, close=close,
        volume=100.0, timestamp=timestamp or 0.0
    )


class TestCandleRangeStopLoss(unittest.TestCase):

    def test_buy_sl_placed_below_recent_candle_low_with_buffer(self):
        """
        BUY: Khi nến M1 gần nhất có low=2597.00 và biên độ=6.00,
        SL phải nằm dưới 2597.00 với đệm ít nhất 25% × 6.00 = 1.50 → SL <= 2595.50.
        """
        profile = get_instrument_profile("XAUUSD")
        # Nến tín hiệu: biên độ $6.00
        recent_bar_range = 6.0
        recent_bar_low = 2597.0
        recent_bar_high = 2603.0

        ws = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            pullback_swing_price=2598.0,
            t1_price=2610.0,
            t2_price=2620.0,
            micro_stall_high=2604.0,
            micro_stall_low=2601.0,
            buffer_pts=profile.min_buffer_points,
            min_sl_distance=profile.min_sl_points,
            sl_multiplier=1.20,
            tp_multiplier=0.90,
            min_profit_distance=profile.min_profit_points,
            recent_candle_range=recent_bar_range,
            recent_candle_low=recent_bar_low,
            recent_candle_high=recent_bar_high,
            candle_buffer_ratio=0.25
        )
        expected_max_sl = recent_bar_low - (recent_bar_range * 0.25)
        self.assertLessEqual(
            ws.S1, expected_max_sl,
            f"SL {ws.S1:.2f} must be below {expected_max_sl:.2f} (recent_candle_low - 25% range buffer)"
        )

    def test_sell_sl_placed_above_recent_candle_high_with_buffer(self):
        """
        SELL: Khi nến M1 gần nhất có high=2603.00 và biên độ=6.00,
        SL phải nằm trên 2603.00 với đệm ít nhất 25% × 6.00 = 1.50 → SL >= 2604.50.
        """
        profile = get_instrument_profile("XAUUSD")
        recent_bar_range = 6.0
        recent_bar_low = 2597.0
        recent_bar_high = 2603.0

        ws = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.PB,
            side=OrderSide.SELL,
            pullback_swing_price=2602.0,
            t1_price=2590.0,
            t2_price=2580.0,
            micro_stall_high=2599.0,
            micro_stall_low=2596.0,
            buffer_pts=profile.min_buffer_points,
            min_sl_distance=profile.min_sl_points,
            sl_multiplier=1.20,
            tp_multiplier=0.90,
            min_profit_distance=profile.min_profit_points,
            recent_candle_range=recent_bar_range,
            recent_candle_low=recent_bar_low,
            recent_candle_high=recent_bar_high,
            candle_buffer_ratio=0.25
        )
        expected_min_sl = recent_bar_high + (recent_bar_range * 0.25)
        self.assertGreaterEqual(
            ws.S1, expected_min_sl,
            f"SL {ws.S1:.2f} must be above {expected_min_sl:.2f} (recent_candle_high + 25% range buffer)"
        )

    def test_sl_distance_expands_with_large_candle_range(self):
        """
        Khi nến M1 gần nhất có biên độ lớn ($5.00), khoảng cách SL từ entry
        phải >= 5.00 × 1.20 (sl_multiplier) = 6.00, vượt qua sàn tĩnh 2.50.
        """
        profile = get_instrument_profile("XAUUSD")
        large_candle_range = 5.0
        entry = 2601.0

        ws = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            pullback_swing_price=2598.0,
            t1_price=2612.0,
            t2_price=2625.0,
            micro_stall_high=2603.0,
            micro_stall_low=entry,
            buffer_pts=0.80,
            min_sl_distance=2.50,  # Sàn tĩnh Gold
            sl_multiplier=1.20,
            tp_multiplier=0.90,
            min_profit_distance=2.0,
            recent_candle_range=large_candle_range,
            recent_candle_low=2598.0,
            candle_buffer_ratio=0.25
        )
        sl_distance = ws.recommended_entry - ws.S1
        expected_min_distance = large_candle_range * 1.20  # 6.0
        self.assertGreaterEqual(
            sl_distance, expected_min_distance,
            f"SL distance {sl_distance:.2f} must be >= {expected_min_distance:.2f} for large candle (range={large_candle_range})"
        )

    def test_small_candle_uses_static_floor(self):
        """
        Khi nến M1 rất nhỏ (biên độ $1.00), sàn tĩnh min_sl_points=2.50
        vẫn được áp dụng để chống spread stop-out.
        """
        profile = get_instrument_profile("XAUUSD")
        small_candle_range = 1.0

        ws = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.TST,
            side=OrderSide.BUY,
            pullback_swing_price=2600.0,
            t1_price=2610.0,
            t2_price=2620.0,
            micro_stall_high=2603.0,
            micro_stall_low=2601.0,
            buffer_pts=0.80,
            min_sl_distance=profile.min_sl_points,  # 2.50
            sl_multiplier=1.0,
            tp_multiplier=0.90,
            min_profit_distance=2.0,
            recent_candle_range=small_candle_range,
            recent_candle_low=2600.5,
            candle_buffer_ratio=0.25
        )
        sl_distance = ws.recommended_entry - ws.S1
        self.assertGreaterEqual(
            sl_distance, profile.min_sl_points,
            f"SL distance {sl_distance:.2f} must be >= min_sl_points {profile.min_sl_points} even for small candles"
        )

    def test_trend_bar_failure_buy_sl_uses_candle_range_buffer(self):
        """
        Module 1 (Trend Bar Failure) BUY: SL nên đặt dưới đáy nến tín hiệu
        với đệm ít nhất 25% biên độ nến (không phải 1 tick = $0.01).
        """
        tick_size = 0.01
        bars = []
        # Tạo 22 nến với EMA rising
        for i in range(20):
            bars.append(_make_bar(open_=2580.0 + i, high=2582.0 + i, low=2579.0 + i, close=2581.5 + i))
        # bar2: Bearish trend bar (thân > 50%)
        bars.append(_make_bar(open_=2601.0, high=2601.5, low=2597.0, close=2597.5))
        # bar1: Makes new low but fails to close as bearish trend bar (biên độ = $4.00)
        bars.append(_make_bar(open_=2598.5, high=2599.5, low=2595.0, close=2598.8))

        triggered, side, entry_price, sl_price, _ = CandlestickEngine.evaluate_trend_bar_failure(bars, tick_size=tick_size)

        if triggered and side == OrderSide.BUY:
            bar1 = bars[-1]
            bar1_range = bar1.high - bar1.low  # 4.50
            expected_min_buffer = max(tick_size * 20, bar1_range * 0.25)
            expected_max_sl = bar1.low - expected_min_buffer
            self.assertLessEqual(
                sl_price, expected_max_sl + 0.01,  # pequeña tolerancia
                f"BUY SL {sl_price:.2f} must be <= {expected_max_sl:.2f} (bar_low - {expected_min_buffer:.2f} buffer)"
            )
            # Đảm bảo buffer >> 1 tick
            actual_buffer = bar1.low - sl_price
            self.assertGreater(
                actual_buffer, tick_size * 5,
                f"SL buffer {actual_buffer:.2f} must be much larger than 1 tick ({tick_size})"
            )

    def test_nr7_buy_sl_uses_candle_range_buffer(self):
        """
        Module 4 (NR7 EMA20) BUY: SL phải đặt dưới đáy nến NR7 với đệm
        tỷ lệ biên độ nến, không phải 1 tick.
        """
        tick_size = 0.01
        bars = []
        # 28 nến với xu hướng tăng đồng đều (EMA20 tăng)
        for i in range(21):
            bars.append(_make_bar(open_=2570.0 + i, high=2573.0 + i, low=2569.5 + i, close=2572.5 + i))
        # 6 nến ngay trên EMA20 để pass bullish filter
        for i in range(6):
            bars.append(_make_bar(open_=2590.5 + i*0.2, high=2592.5 + i*0.2, low=2590.0 + i*0.2, close=2592.0 + i*0.2))
        # Nến NR7 (nhỏ hơn tất cả 6 nến trước): biên độ $1.50
        bars.append(_make_bar(open_=2591.5, high=2592.0, low=2590.5, close=2591.8))

        triggered, side, entry_price, sl_price, _ = CandlestickEngine.evaluate_nr7_ema20(bars, tick_size=tick_size)

        if triggered and side == OrderSide.BUY:
            bar1 = bars[-1]
            bar1_range = bar1.high - bar1.low
            expected_buffer = max(tick_size * 20, bar1_range * 0.25)
            self.assertLessEqual(
                sl_price, bar1.low - expected_buffer + 0.01,
                f"NR7 BUY SL {sl_price:.2f} should be <= {bar1.low - expected_buffer:.2f}"
            )

    def test_wholesale_no_candle_info_falls_back_to_static_floor(self):
        """
        Khi không truyền thông tin biên độ nến (recent_candle_range=0),
        WholesaleEngine phải fallback về sàn tĩnh min_sl_distance một cách an toàn.
        """
        profile = get_instrument_profile("XAUUSD")
        ws = WholesaleEngine.calculate_wholesale_levels(
            setup_type=SetupType.PB,
            side=OrderSide.BUY,
            pullback_swing_price=2598.0,
            t1_price=2610.0,
            t2_price=2620.0,
            micro_stall_high=2603.0,
            micro_stall_low=2601.0,
            buffer_pts=profile.min_buffer_points,
            min_sl_distance=profile.min_sl_points,
            sl_multiplier=1.20,
            tp_multiplier=0.90,
            min_profit_distance=profile.min_profit_points,
            recent_candle_range=0.0  # Không truyền thông tin nến
        )
        sl_distance = ws.recommended_entry - ws.S1
        self.assertGreaterEqual(
            sl_distance, profile.min_sl_points,
            f"Fallback SL distance {sl_distance:.2f} must be >= static floor {profile.min_sl_points}"
        )
        self.assertTrue(ws.is_valid_entry is not None)


if __name__ == "__main__":
    unittest.main()
