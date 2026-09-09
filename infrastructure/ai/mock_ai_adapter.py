"""
Mock AI Engine Adapter
Provides deterministic, strictly schema-compliant Session Planning and Hindsight Audit
Allows immediate local testing and offline development
"""
from typing import Dict, Any, List
import datetime
from core.domain.interfaces.ai_engine import IAIEngine
from core.domain.models import (
    SessionConfig, MarketRegime, HTFZone, Significance, AuditReport
)

class MockAIEngine(IAIEngine):
    async def generate_pre_session_plan(
        self,
        symbol: str,
        quant_baseline_metrics: Dict[str, Any],
        recent_rates_json: Dict[str, Any],
        economic_events: List[Dict[str, Any]],
        retrieved_rag_lessons: List[str]
    ) -> SessionConfig:
        """
        Generates schema-compliant YTCPriceActionSessionConfig
        """
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        session_id = f"sess_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Determine regime based on recent M30 rates
        regime = MarketRegime.SIDEWAYS_RANGE

        from core.domain.models import get_instrument_profile
        profile = get_instrument_profile(symbol)

        # Determine current market price and session extremes from recent M30/M3 rates
        m30_list = recent_rates_json.get("M30", [])
        m3_list = recent_rates_json.get("M3", [])

        if m30_list:
            curr_price = m3_list[-1]["c"] if m3_list else m30_list[-1]["c"]
            session_high = max(b["h"] for b in m30_list)
            session_low = min(b["l"] for b in m30_list)
            zone_width = max((session_high - session_low) * 0.08, curr_price * 0.001)
        else:
            curr_price = profile.base_price
            session_high = curr_price + 20.0
            session_low = curr_price - 20.0
            zone_width = curr_price * 0.001

        digits = profile.digits

        # Major Resistance at session high
        res_maj_high = round(session_high, digits)
        res_maj_low = round(session_high - zone_width, digits)

        # Minor Resistance above current price
        min_offset = max(zone_width * 1.5, 4.0 if "XAU" in symbol.upper() else 0.0010)
        res_min_low = round(curr_price + min_offset, digits)
        res_min_high = round(res_min_low + (zone_width * 0.6), digits)

        # Minor Support below current price
        sup_min_high = round(curr_price - min_offset, digits)
        sup_min_low = round(sup_min_high - (zone_width * 0.6), digits)

        # Major Support at session low
        sup_maj_high = round(session_low + zone_width, digits)
        sup_maj_low = round(session_low, digits)

        res_zones = [
            HTFZone(id="res_maj_1", high=res_maj_high, low=res_maj_low, significance=Significance.MAJOR, zone_type="RESISTANCE"),
            HTFZone(id="res_min_1", high=res_min_high, low=res_min_low, significance=Significance.MINOR, zone_type="RESISTANCE")
        ]
        sup_zones = [
            HTFZone(id="sup_maj_1", high=sup_maj_high, low=sup_maj_low, significance=Significance.MAJOR, zone_type="SUPPORT"),
            HTFZone(id="sup_min_1", high=sup_min_high, low=sup_min_low, significance=Significance.MINOR, zone_type="SUPPORT")
        ]


        # Enabled Setups based on Lance Beggs Matrix for SIDEWAYS_RANGE:
        # SIDEWAYS_RANGE: Enable TST, BOF. Disable PB.
        setups_enabled = {
            "TST": True,
            "BOF": True,
            "BPB": False,
            "PB": False,
            "CPB": False
        }

        execution_rules = {
            "min_rr_ratio_part1": 1.0,
            "require_wholesale_entry": True,
            "max_entry_timeout_bars_1m": 4,
            "stall_min_candles": 3,
            "scratch_timeout_bars_1m": 5,
            "slippage_tolerance_pips": profile.slippage_tolerance_pips
        }


        risk_management = {
            "account_risk_limit_percent": 1.0,
            "part1_risk_percent": 0.5,
            "part2_risk_percent": 0.5,
            "session_drawdown_timeout_percent": 2.0,
            "session_drawdown_hardstop_percent": 3.0,
            "business_drawdown_stop_percent": 20.0
        }

        news_filter = {
            "blackout_before_minutes": 15,
            "blackout_after_minutes": 15,
            "high_impact_events": economic_events
        }

        return SessionConfig(
            session_id=session_id,
            symbol=symbol,
            generated_at=now_iso,
            market_regime=regime,
            resistance_zones=res_zones,
            support_zones=sup_zones,
            setups_enabled=setups_enabled,
            execution_rules=execution_rules,
            risk_management=risk_management,
            news_filter=news_filter
        )

    async def audit_post_session(
        self,
        trading_config_used: Dict[str, Any],
        session_trades_json: List[Dict[str, Any]],
        full_session_ohlcv: Dict[str, Any]
    ) -> AuditReport:
        """
        Answers Lance Beggs 4 questions and produces audit report
        """
        violations = []
        for t in session_trades_json:
            if t.get("entry_outside_wholesale", False):
                violations.append({"trade_id": t.get("trade_id"), "rule": "WHOLESALE_ENTRY_VIOLATED"})

        compliance_score = 1.0 if not violations else max(0.0, 1.0 - (len(violations) * 0.2))

        lessons = [
            "Wait patiently for micro-stall consolidation before firing limit order.",
            "If momentum thrust stalls at midpoint, maintain strict R:R target.",
            "Trapped traders flow failure mandates immediate scratch execution."
        ]

        return AuditReport(
            session_id=trading_config_used.get("session_id", "sess_audited"),
            compliance_score=compliance_score,
            rule_violations=violations,
            hindsight_optimal_trades=[],
            lessons_learned=lessons,
            parameter_adjustments_suggested={"scratch_timeout_bars_1m": 5}
        )
