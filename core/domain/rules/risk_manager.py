"""
Risk Management Engine & Position Allocation
Section 2.3 & 2.4 of YTC Specification
"""
import math
from typing import Tuple, Optional, Dict, Any
from core.domain.models import OrderSide, PositionPart, PositionState, TradeLifecycle

class RiskManager:
    @staticmethod
    def calculate_lot_size(
        balance: float,
        risk_percent: float,
        entry_price: float,
        sl_price: float,
        point_size: float = 0.00001,
        tick_value: float = 1.0,
        min_lot: float = 0.01,
        lot_step: float = 0.01
    ) -> Tuple[float, float, float]:
        """
        Calculates position sizes according to Section 2.3:
          - Risk_USD = Balance * account_risk_limit_percent
          - Dist_Pts = |Entry - S1| / Point_Size
          - Lot_total = Risk_USD / (Dist_Pts * Tick_Value)
          - Split: Lot_Part1 = Lot_total * 0.5, Lot_Part2 = Lot_total * 0.5
        Returns: (lot_total, lot_part1, lot_part2)
        """
        dist_price = abs(entry_price - sl_price)
        dist_pts = max(dist_price / point_size, 1.0)

        risk_usd = balance * (risk_percent / 100.0)
        lot_raw = risk_usd / (dist_pts * tick_value)

        # Normalize to lot_step and clamp by min_lot
        steps = math.floor(lot_raw / lot_step)
        lot_total = max(steps * lot_step, min_lot * 2.0)
        lot_total = round(lot_total, 2)

        lot_p1 = round(lot_total * 0.5, 2)
        lot_p2 = round(lot_total - lot_p1, 2)

        return lot_total, lot_p1, lot_p2

    @staticmethod
    def check_session_drawdown(
        starting_equity: float,
        current_equity: float,
        peak_equity: float,
        timeout_pct: float = 2.0,
        hardstop_pct: float = 3.0,
        business_stop_pct: float = 20.0
    ) -> Tuple[bool, str]:
        """
        Enforces Circuit Breakers:
          - timeout_pct: pause trading for cooloff
          - hardstop_pct: stop session immediately
          - business_stop_pct: business level halt
        """
        session_dd_pct = ((starting_equity - current_equity) / starting_equity) * 100.0
        business_dd_pct = ((peak_equity - current_equity) / peak_equity) * 100.0

        if business_dd_pct >= business_stop_pct:
            return False, f"CIRCUIT_BREAKER_BUSINESS_STOP: Drawdown {business_dd_pct:.2f}% >= {business_stop_pct}%"
        if session_dd_pct >= hardstop_pct:
            return False, f"CIRCUIT_BREAKER_SESSION_HARDSTOP: Drawdown {session_dd_pct:.2f}% >= {hardstop_pct}%"
        if session_dd_pct >= timeout_pct:
            return False, f"CIRCUIT_BREAKER_SESSION_TIMEOUT: Drawdown {session_dd_pct:.2f}% >= {timeout_pct}%"

        return True, "NORMAL"

    @staticmethod
    def evaluate_scratch_rule(
        bars_in_trade: int,
        scratch_timeout_bars: int = 8,
        opposite_momentum_detected: bool = False,
        lwp_orderflow_failed: bool = False,
        unrealized_r: float = 0.0,
        price_progress_pct: float = 0.0
    ) -> Tuple[bool, str]:
        """
        PREMISE_THREATENED (Dynamic Scratch Rule):
          - Fast scratch: Opposite momentum bar closed beyond key node.
          - Orderflow failure: Trapped traders order flow failed upon LWP breach.
          - P&L Aware Timeout:
            - If position has positive progress (unrealized_r >= 0.3 or progress >= 40%),
              dynamically extend scratch timeout (e.g. up to 14 bars) to avoid premature exit.
            - If position is stagnant or negative, scratch strictly at scratch_timeout_bars.
        """
        if opposite_momentum_detected:
            return True, "Premise threatened: Opposite momentum bar closed beyond key node."
        if lwp_orderflow_failed:
            return True, "Premise threatened: Trapped traders order flow failed upon LWP breach."

        # Dynamic timeout extension if position is progressing in profit towards T1
        effective_timeout = scratch_timeout_bars
        if unrealized_r >= 0.3 or price_progress_pct >= 0.40:
            effective_timeout = max(scratch_timeout_bars + 6, 14)

        if bars_in_trade >= effective_timeout:
            return True, f"Scratch timeout reached: {bars_in_trade} bars without directional resolution."

        return False, "PREMISE_INTACT"
