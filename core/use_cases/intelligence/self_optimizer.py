"""
Self-Optimization & Dynamic Parameter Optimization (Section 3.3)
"""
from typing import List, Dict, Any, Tuple

class SelfOptimizerUseCase:
    @staticmethod
    def calculate_mathematical_expectancy(trades: List[Dict[str, Any]]) -> float:
        """
        E = (P_win * avg_W) - (P_loss * avg_L)
        normalized in R-multiples (excluding scratches/breakeven)
        """
        non_scratch = [t for t in trades if t.get("state") not in ["SCRATCHED", "PENDING_ENTRY"]]
        if not non_scratch:
            return 0.0

        wins = [t for t in non_scratch if t.get("r_multiple", 0) > 0]
        losses = [t for t in non_scratch if t.get("r_multiple", 0) < 0]

        total = len(non_scratch)
        p_win = len(wins) / total
        p_loss = len(losses) / total

        avg_w = sum(t["r_multiple"] for t in wins) / len(wins) if wins else 0.0
        avg_l = abs(sum(t["r_multiple"] for t in losses) / len(losses)) if losses else 0.0

        expectancy = (p_win * avg_w) - (p_loss * avg_l)
        return round(expectancy, 4)

    @staticmethod
    def optimize_setups_for_regime(
        regime: str,
        current_setups_enabled: Dict[str, bool],
        trade_history: List[Dict[str, Any]]
    ) -> Dict[str, bool]:
        """
        If setup X generates E < 0 over rolling 20 occurrences:
          setups_enabled[X] = False
        """
        updated_setups = dict(current_setups_enabled)

        for setup_name in current_setups_enabled.keys():
            setup_trades = [
                t for t in trade_history
                if t.get("setup") == setup_name and t.get("regime") == regime
            ][-20:]

            if len(setup_trades) >= 20:
                e = SelfOptimizerUseCase.calculate_mathematical_expectancy(setup_trades)
                if e < 0:
                    updated_setups[setup_name] = False

        return updated_setups
