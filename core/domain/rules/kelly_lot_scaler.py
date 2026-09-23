"""
Module 5: Kelly Criterion Anti-Revenge Lot Scaling
Áp dụng công thức Kelly để điều chỉnh lot size theo win rate thực tế,
tránh tâm lý revenge trading sau thua lỗ liên tiếp.

Dữ liệu thực tế:
  - Win rate: 6.15% (8/130)
  - Avg win: $0.95 | Avg loss: $2.85
  → f* = (0.0615 * 0.333 - 0.9385) / 0.333 = âm → SKIP
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KellyResult:
    """Kết quả tính toán Kelly Criterion cho một lệnh."""
    kelly_fraction: float              # f* theo công thức Kelly (0.0 – 0.25)
    recommended_lot_multiplier: float  # Hệ số nhân vào base lot (0.0 – 1.0)
    action: str                        # 'TRADE' | 'REDUCE' | 'SKIP'
    reason: str                        # Giải thích quyết định
    consecutive_losses: int            # Số lần thua liên tiếp hiện tại
    win_rate_estimate: float           # Win rate ước tính (0.0 – 1.0)
    rr_ratio: float                    # Risk:Reward ratio (avg_win / avg_loss)


class KellyLotScaler:
    """
    Tính toán lot multiplier dựa trên Kelly Criterion.

    Công thức Kelly:
        f* = (p * b - q) / b
    Trong đó:
        p = win_rate
        q = 1 - win_rate  (loss rate)
        b = rr_ratio       (avg_win / avg_loss)

    An toàn: cap tối đa 25% Kelly (f* <= 0.25).
    """

    KELLY_CAP: float = 0.25  # Không bao giờ dùng quá 25% Kelly

    # ------------------------------------------------------------------ #
    # Core formula                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def calculate_kelly_fraction(win_rate: float, rr_ratio: float) -> float:
        """
        Tính Kelly fraction f*.

        Args:
            win_rate: Tỷ lệ thắng (0.0 – 1.0).
            rr_ratio: Avg_win / Avg_loss (> 0).

        Returns:
            Kelly fraction, giới hạn trong [0.0, KELLY_CAP].
            Trả về 0.0 nếu expectancy âm (edge không có lợi).
        """
        if rr_ratio <= 0:
            return 0.0

        loss_rate = 1.0 - win_rate
        # f* = (p * b - q) / b
        kelly = (win_rate * rr_ratio - loss_rate) / rr_ratio

        if kelly <= 0.0:
            return 0.0

        # Cap tối đa 25% để tránh over-leverage
        return min(kelly, KellyLotScaler.KELLY_CAP)

    # ------------------------------------------------------------------ #
    # Decision engine                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_lot_multiplier(
        win_rate: float,
        rr_ratio: float,
        consecutive_losses: int = 0,
    ) -> KellyResult:
        """
        Tính hệ số nhân lot và quyết định TRADE / REDUCE / SKIP.

        Logic ưu tiên (từ trên xuống):
          1. Nếu consecutive_losses >= 5  → SKIP (cooldown bắt buộc)
          2. Nếu kelly <= 0.0              → SKIP (edge âm, không nên giao dịch)
          3. Nếu consecutive_losses >= 3  → REDUCE (half Kelly)
          4. Còn lại                      → TRADE (full Kelly)

        Args:
            win_rate:           Tỷ lệ thắng ước tính từ N lệnh gần nhất.
            rr_ratio:           Avg_win / Avg_loss.
            consecutive_losses: Số lần thua liên tiếp hiện tại.

        Returns:
            KellyResult với đầy đủ thông tin.
        """
        kelly = KellyLotScaler.calculate_kelly_fraction(win_rate, rr_ratio)

        # --- Rule 1: Quá nhiều thua liên tiếp → bắt buộc dừng ---
        if consecutive_losses >= 5:
            return KellyResult(
                kelly_fraction=kelly,
                recommended_lot_multiplier=0.0,
                action="SKIP",
                reason=(
                    f"COOLDOWN: {consecutive_losses} lần thua liên tiếp "
                    f"(ngưỡng dừng = 5). Nghỉ ngơi, xem lại setup."
                ),
                consecutive_losses=consecutive_losses,
                win_rate_estimate=win_rate,
                rr_ratio=rr_ratio,
            )

        # --- Rule 2: Edge âm → bỏ qua lệnh ---
        if kelly <= 0.0:
            return KellyResult(
                kelly_fraction=0.0,
                recommended_lot_multiplier=0.0,
                action="SKIP",
                reason=(
                    f"EDGE AM: win_rate={win_rate:.1%}, RR={rr_ratio:.2f} "
                    f"-> f*<=0, khong co loi the toan hoc de giao dich."
                ),
                consecutive_losses=consecutive_losses,
                win_rate_estimate=win_rate,
                rr_ratio=rr_ratio,
            )

        # --- Rule 3: Thua 3-4 liên tiếp → giảm lot (half Kelly) ---
        if consecutive_losses >= 3:
            half_kelly = kelly * 0.5
            return KellyResult(
                kelly_fraction=kelly,
                recommended_lot_multiplier=half_kelly,
                action="REDUCE",
                reason=(
                    f"HALF KELLY: {consecutive_losses} lần thua liên tiếp "
                    f"→ giảm lot xuống {half_kelly:.1%} (f*={kelly:.3f} × 0.5)."
                ),
                consecutive_losses=consecutive_losses,
                win_rate_estimate=win_rate,
                rr_ratio=rr_ratio,
            )

        # --- Rule 4: Bình thường → full Kelly ---
        return KellyResult(
            kelly_fraction=kelly,
            recommended_lot_multiplier=kelly,
            action="TRADE",
            reason=(
                f"TRADE: f*={kelly:.3f} (win_rate={win_rate:.1%}, "
                f"RR={rr_ratio:.2f}, losses={consecutive_losses})."
            ),
            consecutive_losses=consecutive_losses,
            win_rate_estimate=win_rate,
            rr_ratio=rr_ratio,
        )

    # ------------------------------------------------------------------ #
    # Apply to concrete lot size                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def apply_to_lot(
        base_lot: float,
        result: KellyResult,
        min_lot: float = 0.01,
    ) -> float:
        """
        Áp dụng KellyResult vào base lot để lấy lot thực tế.

        Args:
            base_lot:  Lot cơ sở (ví dụ: 0.05 từ Dynamic Lot Sizer).
            result:    Kết quả từ get_lot_multiplier().
            min_lot:   Lot tối thiểu theo broker (mặc định 0.01).

        Returns:
            Lot thực tế (rounded 2 decimal).
            Trả về 0.0 nếu action == 'SKIP'.
        """
        if result.action == "SKIP":
            return 0.0

        scaled = base_lot * result.recommended_lot_multiplier
        final = max(scaled, min_lot)
        return round(final, 2)
