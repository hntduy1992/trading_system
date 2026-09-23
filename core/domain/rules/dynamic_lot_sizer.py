"""
Module 3: Dynamic Lot Sizer
============================
Calculates the minimum lot size required to achieve a target NET profit
on XAUUSD after deducting spread and round-trip commission costs.

XAUUSD specifics (per standard lot = 100 oz):
  - 1 point  = $0.01 price move  → $1.00 per lot
  - Typical spread  : ~0.30–0.50 points (≈ $0.30–$0.50/lot)
  - Typical commission: $3.50–$7.00/lot round-trip

Reality check (balance ~$400, risk 1% = $4):
  SL 2–5 pts → lot = $4 / (SL_pts × $1/lot) = 0.80–2.0 lot
  Using 0.01 lot is effectively risking only $0.02–$0.05 → too small!

Usage:
    result = DynamicLotSizer.calculate_lot_for_net_profit(
        entry=4344.0, t1=4348.0, sl=4342.0, balance=400.0
    )
    print(DynamicLotSizer.format_summary(result, 4344.0, 4348.0, 4342.0))
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Constants – XAUUSD market microstructure
# ---------------------------------------------------------------------------
XAUUSD_POINT_VALUE: float = 1.0         # USD per lot per 0.01-pt (1 point)
EST_SPREAD_COST_PER_LOT: float = 0.50   # points of spread (≈ $0.50/lot)
EST_COMM_PER_LOT_RT: float = 7.0        # USD round-trip commission per lot


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class LotCalculationResult:
    """Full breakdown of a lot-size calculation."""

    lot_size: float                     # Final recommended lot size
    expected_gross_usd: float           # Gross P&L before costs (USD)
    expected_net_usd: float             # Net P&L after spread + commission (USD)
    spread_cost_usd: float              # Total spread cost at this lot size (USD)
    commission_usd: float               # Total RT commission at this lot size (USD)
    risk_usd: float                     # Maximum risk at this lot size (USD)
    is_feasible: bool                   # True if net profit target is achievable
    infeasible_reason: str = ""         # Non-empty when is_feasible == False

    # Derived convenience attributes (populated post-init)
    net_per_lot: float = field(default=0.0, init=False)
    lot_for_profit: float = field(default=0.0, init=False)
    lot_risk_max: float = field(default=0.0, init=False)

    def __repr__(self) -> str:          # pragma: no cover
        status = "FEASIBLE" if self.is_feasible else f"INFEASIBLE({self.infeasible_reason})"
        return (
            f"LotCalcResult(lot={self.lot_size:.2f}, "
            f"gross=${self.expected_gross_usd:.2f}, "
            f"net=${self.expected_net_usd:.2f}, "
            f"spread=${self.spread_cost_usd:.2f}, "
            f"comm=${self.commission_usd:.2f}, "
            f"risk=${self.risk_usd:.2f}, "
            f"{status})"
        )


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------
class DynamicLotSizer:
    """
    Stateless calculator that determines the lot size needed to achieve a
    minimum NET profit on XAUUSD after spread and round-trip commission.

    All price inputs are in XAUUSD *points* unit (i.e. the raw price,
    e.g. 4344.00), and the engine converts to dollar P&L using POINT_VALUE.
    """

    # Expose module-level constants as class attributes for easy access.
    POINT_VALUE: float = XAUUSD_POINT_VALUE
    SPREAD_POINTS: float = EST_SPREAD_COST_PER_LOT
    COMMISSION_RT: float = EST_COMM_PER_LOT_RT

    @classmethod
    def calculate_lot_for_net_profit(
        cls,
        entry: float,
        t1: float,
        sl: float,
        balance: float,
        min_net_profit_usd: float = 2.0,
        risk_pct: float = 1.0,
        min_lot: float = 0.01,
        max_lot: float = 1.0,
    ) -> LotCalculationResult:
        """
        Calculate the minimum lot size that yields ≥ *min_net_profit_usd*
        after spread and commission, while staying within the account risk
        limit defined by *risk_pct*.

        Parameters
        ----------
        entry             : Trade entry price (XAUUSD)
        t1                : First profit target price
        sl                : Stop-loss price
        balance           : Current account balance (USD)
        min_net_profit_usd: Minimum acceptable NET profit per trade (default $2)
        risk_pct          : Maximum risk as % of balance (default 1 %)
        min_lot           : Broker minimum lot (default 0.01)
        max_lot           : Hard cap on lot size (default 1.0)

        Returns
        -------
        LotCalculationResult with full cost breakdown
        """
        # ------------------------------------------------------------------
        # Step 1 – Reward in points (price units)
        # ------------------------------------------------------------------
        reward_points: float = abs(t1 - entry)

        if reward_points <= 0:
            return cls._infeasible(
                reason="T1 equals entry – zero reward distance",
                entry=entry, t1=t1, sl=sl, balance=balance,
                risk_pct=risk_pct, min_lot=min_lot,
            )

        # ------------------------------------------------------------------
        # Step 2 – Net P&L per 1 lot
        #   gross_per_lot  = reward_points × POINT_VALUE
        #   cost_per_lot   = spread_cost + commission
        #   net_per_lot    = gross_per_lot - cost_per_lot
        # ------------------------------------------------------------------
        gross_per_lot: float = reward_points * cls.POINT_VALUE
        spread_cost_per_lot: float = cls.SPREAD_POINTS * cls.POINT_VALUE
        commission_per_lot: float = cls.COMMISSION_RT
        cost_per_lot: float = spread_cost_per_lot + commission_per_lot
        net_per_lot: float = gross_per_lot - cost_per_lot

        # ------------------------------------------------------------------
        # Step 3 – Feasibility check: T1 must cover costs
        # ------------------------------------------------------------------
        if net_per_lot <= 0:
            result = cls._infeasible(
                reason=(
                    f"T1 too close to entry: reward={reward_points:.2f}pts → "
                    f"gross=${gross_per_lot:.2f}/lot, but costs=${cost_per_lot:.2f}/lot "
                    f"(spread=${spread_cost_per_lot:.2f} + comm=${commission_per_lot:.2f})"
                ),
                entry=entry, t1=t1, sl=sl, balance=balance,
                risk_pct=risk_pct, min_lot=min_lot,
            )
            result.net_per_lot = net_per_lot
            return result

        # ------------------------------------------------------------------
        # Step 4 – Lot required to reach min_net_profit_usd
        # ------------------------------------------------------------------
        lot_for_profit: float = min_net_profit_usd / net_per_lot

        # ------------------------------------------------------------------
        # Step 5 – Max lot allowed by risk budget
        #   risk_usd  = balance × risk_pct / 100
        #   sl_points = |entry - sl|
        #   lot_risk  = risk_usd / (sl_points × POINT_VALUE)
        # ------------------------------------------------------------------
        sl_points: float = abs(entry - sl)
        risk_usd: float = balance * (risk_pct / 100.0)

        if sl_points > 0:
            lot_risk_max: float = risk_usd / (sl_points * cls.POINT_VALUE)
        else:
            # No SL defined – fall back to max_lot
            lot_risk_max = max_lot

        # ------------------------------------------------------------------
        # Step 6 – Final lot: at least lot_for_profit, capped by risk/max
        # ------------------------------------------------------------------
        lot_candidate: float = max(lot_for_profit, min_lot)
        lot_final: float = min(lot_candidate, lot_risk_max, max_lot)
        lot_final = math.floor(lot_final * 100) / 100   # truncate to 2 dp
        lot_final = max(lot_final, min_lot)              # never below min

        # ------------------------------------------------------------------
        # Step 7 – Compute expected P&L at the chosen lot size
        # ------------------------------------------------------------------
        expected_gross = lot_final * gross_per_lot
        expected_spread_cost = lot_final * spread_cost_per_lot
        expected_commission = lot_final * commission_per_lot
        expected_net = expected_gross - expected_spread_cost - expected_commission
        actual_risk_usd = lot_final * sl_points * cls.POINT_VALUE

        is_feasible = expected_net >= min_net_profit_usd

        infeasible_reason = ""
        if not is_feasible:
            if lot_final == lot_risk_max or lot_final == max_lot:
                infeasible_reason = (
                    f"Risk/max-lot cap ({lot_final:.2f}) prevents reaching "
                    f"net ${min_net_profit_usd:.2f} target "
                    f"(achievable net=${expected_net:.2f})"
                )
            else:
                infeasible_reason = (
                    f"Net ${expected_net:.2f} < target ${min_net_profit_usd:.2f} "
                    f"even at min lot {min_lot}"
                )

        result = LotCalculationResult(
            lot_size=lot_final,
            expected_gross_usd=round(expected_gross, 4),
            expected_net_usd=round(expected_net, 4),
            spread_cost_usd=round(expected_spread_cost, 4),
            commission_usd=round(expected_commission, 4),
            risk_usd=round(actual_risk_usd, 4),
            is_feasible=is_feasible,
            infeasible_reason=infeasible_reason,
        )
        result.net_per_lot = round(net_per_lot, 4)
        result.lot_for_profit = round(lot_for_profit, 4)
        result.lot_risk_max = round(lot_risk_max, 4)
        return result

    # ------------------------------------------------------------------
    # Formatting helper
    # ------------------------------------------------------------------
    @staticmethod
    def format_summary(
        result: LotCalculationResult,
        entry: float,
        t1: float,
        sl: float,
    ) -> str:
        """Return a human-readable summary string suitable for logging."""
        reward_pts = abs(t1 - entry)
        sl_pts = abs(entry - sl)
        status = "✅ FEASIBLE" if result.is_feasible else f"❌ INFEASIBLE: {result.infeasible_reason}"
        lines = [
            "=" * 60,
            "  DYNAMIC LOT SIZER – XAUUSD",
            "=" * 60,
            f"  Entry : {entry:.2f}  T1: {t1:.2f}  SL: {sl:.2f}",
            f"  Reward: {reward_pts:.2f} pts  |  SL dist: {sl_pts:.2f} pts",
            "-" * 60,
            f"  Net P&L per lot : ${result.net_per_lot:.4f}",
            f"  Lot for profit  : {result.lot_for_profit:.4f}",
            f"  Lot risk max    : {result.lot_risk_max:.4f}",
            "-" * 60,
            f"  ▶ Recommended lot : {result.lot_size:.2f}",
            f"    Gross            : ${result.expected_gross_usd:.2f}",
            f"    Spread cost      : -${result.spread_cost_usd:.2f}",
            f"    Commission (RT)  : -${result.commission_usd:.2f}",
            f"    NET P&L          : ${result.expected_net_usd:.2f}",
            f"    Max risk         : ${result.risk_usd:.2f}",
            "-" * 60,
            f"  Status: {status}",
            "=" * 60,
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @classmethod
    def _infeasible(
        cls,
        reason: str,
        entry: float,
        t1: float,
        sl: float,
        balance: float,
        risk_pct: float,
        min_lot: float,
    ) -> LotCalculationResult:
        """Build a zero-valued infeasible result."""
        sl_points = abs(entry - sl)
        risk_usd = balance * (risk_pct / 100.0)
        actual_risk = min_lot * sl_points * cls.POINT_VALUE
        result = LotCalculationResult(
            lot_size=min_lot,
            expected_gross_usd=0.0,
            expected_net_usd=0.0,
            spread_cost_usd=0.0,
            commission_usd=0.0,
            risk_usd=round(actual_risk, 4),
            is_feasible=False,
            infeasible_reason=reason,
        )
        result.lot_risk_max = round(risk_usd / max(sl_points * cls.POINT_VALUE, 1e-9), 4)
        return result
