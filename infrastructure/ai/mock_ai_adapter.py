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

        # Determine regime and trend based on recent M3 and M30 rates
        from core.domain.models import Bar, get_instrument_profile
        from core.domain.rules.swing_detector import SwingDetector
        profile = get_instrument_profile(symbol)

        m30_list = recent_rates_json.get("M30", [])
        m3_list = recent_rates_json.get("M3", [])

        bars_m30 = [Bar(timestamp=b["time"], open=b["o"], high=b["h"], low=b["l"], close=b["c"], volume=100, timeframe="M30") for b in m30_list]
        bars_m3 = [Bar(timestamp=b["time"], open=b["o"], high=b["h"], low=b["l"], close=b["c"], volume=100, timeframe="M3") for b in m3_list]

        if m3_list:
            curr_price = m3_list[-1]["c"]
        elif m30_list:
            curr_price = m30_list[-1]["c"]
        else:
            curr_price = profile.base_price

        # Detect trend on M3 TTF
        swings_3m = SwingDetector.detect_swings(bars_m3) if bars_m3 else []
        trend_str = SwingDetector.evaluate_trend(swings_3m, curr_price) if swings_3m else "SIDEWAYS"

        if "UPTREND" in trend_str or "DOWNTREND" in trend_str:
            regime = MarketRegime.TRENDING_STEADY
            setups_enabled = {
                "TST": False,
                "BOF": False,
                "BPB": True,
                "PB": True,
                "CPB": True
            }
        elif "SIDEWAYS" in trend_str:
            regime = MarketRegime.SIDEWAYS_RANGE
            setups_enabled = {
                "TST": True,
                "BOF": True,
                "BPB": False,
                "PB": False,
                "CPB": False
            }
        else:
            # Choppy / Undetermined: enable all active setups to catch valid PA triggers
            regime = MarketRegime.SIDEWAYS_RANGE
            setups_enabled = {
                "TST": True,
                "BOF": True,
                "BPB": True,
                "PB": True,
                "CPB": True
            }

        digits = profile.digits

        if m30_list:
            session_high = max(b["h"] for b in m30_list)
            session_low = min(b["l"] for b in m30_list)
            zone_width = max((session_high - session_low) * 0.05, profile.sr_proximity_points * 1.5)
        else:
            session_high = curr_price + profile.default_t2_points
            session_low = curr_price - profile.default_t2_points
            zone_width = profile.sr_proximity_points * 1.5

        # Extract real structural S/R zones from M30 / M3 swings
        swings_30m = SwingDetector.detect_swings(bars_m30) if bars_m30 else []
        sh_above = [s for s in (swings_30m or swings_3m) if s.price > curr_price]
        sl_below = [s for s in (swings_30m or swings_3m) if s.price < curr_price]

        # Resistance zones
        if sh_above:
            nearest_sh = min(sh_above, key=lambda s: s.price)
            res_min_high = round(nearest_sh.price + zone_width * 0.3, digits)
            res_min_low = round(nearest_sh.price - zone_width * 0.3, digits)
        else:
            res_min_low = round(curr_price + profile.sr_proximity_points * 2, digits)
            res_min_high = round(res_min_low + zone_width, digits)

        res_maj_high = round(session_high, digits)
        res_maj_low = round(session_high - zone_width, digits)

        # Support zones
        if sl_below:
            nearest_sl = max(sl_below, key=lambda s: s.price)
            sup_min_high = round(nearest_sl.price + zone_width * 0.3, digits)
            sup_min_low = round(nearest_sl.price - zone_width * 0.3, digits)
        else:
            sup_min_high = round(curr_price - profile.sr_proximity_points * 2, digits)
            sup_min_low = round(sup_min_high - zone_width, digits)

        sup_maj_low = round(session_low, digits)
        sup_maj_high = round(session_low + zone_width, digits)

        res_zones = [
            HTFZone(id="res_maj_1", high=res_maj_high, low=res_maj_low, significance=Significance.MAJOR, zone_type="RESISTANCE"),
            HTFZone(id="res_min_1", high=res_min_high, low=res_min_low, significance=Significance.MINOR, zone_type="RESISTANCE")
        ]
        sup_zones = [
            HTFZone(id="sup_maj_1", high=sup_maj_high, low=sup_maj_low, significance=Significance.MAJOR, zone_type="SUPPORT"),
            HTFZone(id="sup_min_1", high=sup_min_high, low=sup_min_low, significance=Significance.MINOR, zone_type="SUPPORT")
        ]

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
