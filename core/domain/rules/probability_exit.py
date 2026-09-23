"""
Module 4: Probability-Based Exit Engine
========================================
Replaces the static profit-protector exit logic with a probabilistic
continuation model.  At each bar the engine scores the probability that
price will continue towards T1, and decides the most appropriate action.

Decision thresholds:
  P ≥ 0.70  → HOLD          (high confidence in continuation)
  P 0.50–0.70 → BREAKEVEN   (move SL to entry, protect capital)
  P 0.30–0.50 → TAKE_PROFIT (harvest partial / full unrealised profit now)
  P < 0.30  → EXIT_ALL      (momentum lost, exit immediately)

Override rule:
  If unrealized_r ≥ 1.5R AND bars_stalled ≥ 10 → force TAKE_PROFIT

Usage:
    engine = ProbabilityExitEngine()
    decision = engine.evaluate(
        curr_price=4346.5, entry_price=4344.0, sl_price=4342.0,
        t1_price=4348.0, side="BUY",
        unrealized_r=0.8, bars_in_trade=5,
        bars_stalled=2, momentum_ratio=0.6, bar_quality=0.65,
    )
    print(decision.action, decision.probability_continue, decision.reason)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class ExitAction(enum.Enum):
    """Possible exit decisions returned by the engine."""
    HOLD         = "HOLD"          # Stay in trade, no change
    BREAKEVEN    = "BREAKEVEN"     # Move SL to entry price
    TAKE_PROFIT  = "TAKE_PROFIT"   # Close trade, take profit now
    EXIT_ALL     = "EXIT_ALL"      # Emergency exit, close immediately


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class ExitDecision:
    """Full exit decision with diagnostic metadata."""

    action: ExitAction              # Recommended action
    probability_continue: float     # Estimated P(price continues to T1) [0,1]
    reason: str                     # Human-readable explanation
    suggested_sl: Optional[float]   # New SL level (set for BREAKEVEN action)

    def __repr__(self) -> str:      # pragma: no cover
        sl_info = f", suggested_sl={self.suggested_sl:.5f}" if self.suggested_sl is not None else ""
        return (
            f"ExitDecision(action={self.action.value}, "
            f"P={self.probability_continue:.3f}, "
            f"reason='{self.reason}'"
            f"{sl_info})"
        )


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------
class ProbabilityExitEngine:
    """
    Stateless engine that scores continuation probability and emits an
    exit decision on every bar evaluation.

    Probability formula (weighted sum, clamped to [0, 1]):
        P = 0.40 × momentum_ratio
          + 0.35 × bar_quality
          + 0.25 × (1 / (1 + bars_stalled / 5))

    Weights interpretation:
        momentum_ratio  – directional strength of recent bars (0=weakest, 1=strongest)
        bar_quality     – quality/body ratio of the last closed bar (0=doji, 1=full body)
        bars_stalled    – bars where price has not made new progress toward T1
                          (decays the stall component via a mild logistic curve)
    """

    # Decision boundaries
    THRESHOLD_HOLD: float        = 0.70
    THRESHOLD_BREAKEVEN: float   = 0.50
    THRESHOLD_TAKE_PROFIT: float = 0.30

    # Override rule parameters
    OVERRIDE_R_THRESHOLD: float    = 1.5   # Unrealised R to trigger override
    OVERRIDE_STALL_BARS: int       = 10    # Stalled bars to trigger override

    @staticmethod
    def calculate_p_continuation(
        momentum_ratio: float,
        bar_quality: float,
        bars_stalled: int,
    ) -> float:
        """
        Compute the probability that price continues toward T1.

        Parameters
        ----------
        momentum_ratio : float [0, 1] – recent directional momentum strength
        bar_quality    : float [0, 1] – quality of the last bar (body ratio etc.)
        bars_stalled   : int  ≥ 0    – number of bars without new progress

        Returns
        -------
        float in [0.0, 1.0]
        """
        momentum_ratio = max(0.0, min(1.0, momentum_ratio))
        bar_quality    = max(0.0, min(1.0, bar_quality))
        bars_stalled   = max(0, bars_stalled)

        stall_component: float = 1.0 / (1.0 + bars_stalled / 5.0)

        p: float = (
            0.40 * momentum_ratio
            + 0.35 * bar_quality
            + 0.25 * stall_component
        )
        return max(0.0, min(1.0, p))

    def evaluate(
        self,
        curr_price: float,
        entry_price: float,
        sl_price: float,
        t1_price: float,
        side: str,
        unrealized_r: float,
        bars_in_trade: int,
        bars_stalled: int = 0,
        momentum_ratio: float = 0.5,
        bar_quality: float = 0.5,
    ) -> ExitDecision:
        """
        Evaluate the current trade position and decide the appropriate action.

        Parameters
        ----------
        curr_price      : Current market price
        entry_price     : Original trade entry price
        sl_price        : Current stop-loss price
        t1_price        : First profit target price
        side            : "BUY" or "SELL" (case-insensitive)
        unrealized_r    : Current unrealised profit in R-multiples
                          (e.g. 1.0 R = risk amount fully earned)
        bars_in_trade   : Total bars since entry
        bars_stalled    : Bars since last new high-water mark toward T1
        momentum_ratio  : Directional momentum strength [0, 1]
        bar_quality     : Last bar quality score [0, 1]

        Returns
        -------
        ExitDecision with recommended action and full diagnostics
        """
        side_upper = side.upper()

        # Compute base probability
        p = self.calculate_p_continuation(momentum_ratio, bar_quality, bars_stalled)

        # ------------------------------------------------------------------
        # Override rule: large profit + prolonged stall → harvest now
        # ------------------------------------------------------------------
        if unrealized_r >= self.OVERRIDE_R_THRESHOLD and bars_stalled >= self.OVERRIDE_STALL_BARS:
            return ExitDecision(
                action=ExitAction.TAKE_PROFIT,
                probability_continue=p,
                reason=(
                    f"Override: unrealized_r={unrealized_r:.2f}R ≥ {self.OVERRIDE_R_THRESHOLD}R "
                    f"and stalled {bars_stalled} ≥ {self.OVERRIDE_STALL_BARS} bars – harvest profit"
                ),
                suggested_sl=None,
            )

        # ------------------------------------------------------------------
        # Probability-driven decisions
        # ------------------------------------------------------------------
        if p >= self.THRESHOLD_HOLD:
            return ExitDecision(
                action=ExitAction.HOLD,
                probability_continue=p,
                reason=f"P={p:.3f} ≥ {self.THRESHOLD_HOLD} – strong continuation, hold position",
                suggested_sl=None,
            )

        if p >= self.THRESHOLD_BREAKEVEN:
            return ExitDecision(
                action=ExitAction.BREAKEVEN,
                probability_continue=p,
                reason=(
                    f"P={p:.3f} ∈ [{self.THRESHOLD_BREAKEVEN}, {self.THRESHOLD_HOLD}) – "
                    f"moderate risk, move SL to breakeven"
                ),
                suggested_sl=entry_price,
            )

        if p >= self.THRESHOLD_TAKE_PROFIT:
            return ExitDecision(
                action=ExitAction.TAKE_PROFIT,
                probability_continue=p,
                reason=(
                    f"P={p:.3f} ∈ [{self.THRESHOLD_TAKE_PROFIT}, {self.THRESHOLD_BREAKEVEN}) – "
                    f"momentum fading, take profit now"
                ),
                suggested_sl=None,
            )

        # p < THRESHOLD_TAKE_PROFIT
        return ExitDecision(
            action=ExitAction.EXIT_ALL,
            probability_continue=p,
            reason=(
                f"P={p:.3f} < {self.THRESHOLD_TAKE_PROFIT} – "
                f"momentum collapsed, exit all immediately"
            ),
            suggested_sl=None,
        )

    # ------------------------------------------------------------------
    # Convenience: batch evaluate a series of snapshots
    # ------------------------------------------------------------------
    def evaluate_series(self, snapshots: list[dict]) -> list[ExitDecision]:
        """
        Evaluate a list of bar snapshots.

        Each dict must contain the same keyword arguments accepted by
        :meth:`evaluate`.  Useful for back-testing or bulk replay.
        """
        return [self.evaluate(**snap) for snap in snapshots]
