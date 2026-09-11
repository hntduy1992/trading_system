"""
Mock AI Engine Adapter
Provides deterministic, strictly schema-compliant Session Planning and Hindsight Audit
Allows immediate local testing and offline development
"""
from typing import Dict, Any, List
import datetime
from core.domain.interfaces.ai_engine import IAIEngine
from core.domain.models import (
    SessionConfig, MarketRegime, HTFZone, Significance, AuditReport, PreEntryEvaluation
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

    async def evaluate_candidate_trade(
        self,
        candidate_context: Dict[str, Any],
        trading_config: Dict[str, Any],
        recent_bars: Dict[str, Any]
    ) -> PreEntryEvaluation:
        """
        Deterministic Rule-Based Pre-Entry Risk Evaluator:
        Validates whether setup matches regime, respects wholesale boundary,
        and has sufficient R:R before dispatching to broker.
        """
        setup = candidate_context.get("setup", "")
        side = candidate_context.get("side", "")
        order_price = candidate_context.get("order_price", 0.0)
        sl = candidate_context.get("sl", 0.0)
        tp1 = candidate_context.get("tp1", 0.0)
        wholesale = candidate_context.get("wholesale") or {}
        regime = trading_config.get("market_regime", "SIDEWAYS_RANGE")
        setups_enabled = trading_config.get("setups_enabled", {})

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        concerns = []

        # 1. Setup Enablement Check
        if setups_enabled and not setups_enabled.get(setup, True):
            return PreEntryEvaluation(
                approved=False,
                confidence=0.95,
                reason=f"Setup {setup} bị vô hiệu hóa trong kế hoạch phiên cho chế độ thị trường {regime}.",
                concerns=[f"Setup {setup} is disabled in active Trading Plan for {regime}"],
                evaluated_at=now_str,
                model_name="MockAIEngine-Deterministic"
            )

        # 2. Wholesale Validity Check
        if wholesale and wholesale.get("is_valid_entry") is False:
            return PreEntryEvaluation(
                approved=False,
                confidence=0.90,
                reason=f"Điểm vào {order_price} không đạt tiêu chuẩn Wholesale (vượt LWP={wholesale.get('LWP')} hoặc LRP={wholesale.get('LRP')}).",
                concerns=["Entry price is outside wholesale price boundary"],
                evaluated_at=now_str,
                model_name="MockAIEngine-Deterministic"
            )

        # 3. Minimum R:R Check
        rr = wholesale.get("rr_ratio_part1", 0.0)
        if rr > 0 and rr < 1.0:
            return PreEntryEvaluation(
                approved=False,
                confidence=0.88,
                reason=f"Tỷ lệ R:R Part 1 ({rr:.2f}) thấp hơn mức tối thiểu 1.0 bắt buộc theo YTC.",
                concerns=[f"Part 1 Risk-to-Reward {rr:.2f} < 1.0 minimum constraint"],
                evaluated_at=now_str,
                model_name="MockAIEngine-Deterministic"
            )

        # 4. Check SL distance safety
        risk_dist = abs(order_price - sl)
        if risk_dist <= 0:
            return PreEntryEvaluation(
                approved=False,
                confidence=0.99,
                reason="Khoảng cách Stop Loss không hợp lệ (bằng hoặc trùng với điểm vào).",
                concerns=["Invalid stop loss level"],
                evaluated_at=now_str,
                model_name="MockAIEngine-Deterministic"
            )

        # Approved
        return PreEntryEvaluation(
            approved=True,
            confidence=0.86,
            reason=f"Setup {setup} {side} đạt chuẩn Price Action YTC: Nằm trọn trong vùng giá sỉ Wholesale (R:R={rr:.1f}), bám sát cấu trúc sóng M3 và có nén micro-stall bảo vệ.",
            concerns=[],
            suggested_modifications={},
            evaluated_at=now_str,
            model_name="MockAIEngine-Deterministic"
        )

    async def audit_post_session(
        self,
        trading_config_used: Dict[str, Any],
        session_trades_json: List[Dict[str, Any]],
        full_session_ohlcv: Dict[str, Any]
    ) -> AuditReport:
        """
        Deterministic Rule-Based Hindsight Auditor:
        Answers Lance Beggs 4 questions, evaluates entry contexts against Trading Plan,
        calculates compliance, critiques plan parameters, and derives actionable lessons.
        """
        violations = []
        trade_evaluations = []
        session_id = trading_config_used.get("session_id", "sess_audited")
        regime = trading_config_used.get("market_regime", "SIDEWAYS_RANGE")
        setups_enabled = trading_config_used.get("setups_enabled", {})

        wins = 0
        losses = 0
        scratches = 0

        for t in session_trades_json:
            trade_id = t.get("trade_id", "unknown")
            entry_ctx = t.get("entry_context") or {}
            close_ctx = t.get("close_context") or {}
            setup = t.get("setup") or entry_ctx.get("setup", "UNKNOWN")
            close_state = t.get("state") or close_ctx.get("close_state", "UNKNOWN")
            wholesale = entry_ctx.get("wholesale") or {}

            eval_notes = []
            trade_score = 1.0

            # 1. Rule Check: Setup must be explicitly enabled in Trading Plan
            if setup != "MANUAL" and setups_enabled and not setups_enabled.get(setup, True):
                violations.append({
                    "trade_id": trade_id,
                    "rule": "SETUP_NOT_ENABLED_IN_PLAN",
                    "detail": f"Setup {setup} was executed but disabled in session plan for regime {regime}"
                })
                eval_notes.append(f"Vi phạm: Setup {setup} không nằm trong danh mục kích hoạt của phiên.")
                trade_score -= 0.3

            # 2. Rule Check: Wholesale entry validity
            if wholesale and wholesale.get("is_valid_entry") is False:
                violations.append({
                    "trade_id": trade_id,
                    "rule": "WHOLESALE_ENTRY_VIOLATED",
                    "detail": f"Entry failed wholesale boundary (LWP={wholesale.get('LWP')}, LRP={wholesale.get('LRP')})"
                })
                eval_notes.append("Vi phạm: Điểm vào vượt ra ngoài vùng giá sỉ Wholesale (tỷ lệ R:R < 1.0).")
                trade_score -= 0.3

            # 3. Outcome categorization
            if close_state == "FULLY_CLOSED":
                wins += 1
                eval_notes.append("Kết quả: Đạt mục tiêu lợi nhuận (T1 / T2 Hit) theo đúng kế hoạch.")
            elif close_state == "STOPPED_OUT":
                losses += 1
                eval_notes.append("Kết quả: Dừng lỗ (Stop Out). Cần rà soát lại khoảng cách đệm S1 và độ nén Stall.")
            elif close_state == "SCRATCHED":
                scratches += 1
                eval_notes.append(f"Kết quả: Thoát lệnh sớm bảo toàn vốn (Scratch: {close_ctx.get('close_reason', 'Premise threatened')}).")
            else:
                eval_notes.append(f"Trạng thái: {close_state}")

            trade_evaluations.append({
                "trade_id": trade_id,
                "setup": setup,
                "side": t.get("side", ""),
                "state": close_state,
                "score": max(0.0, round(trade_score, 2)),
                "entry_time": entry_ctx.get("time_str", ""),
                "entry_price": entry_ctx.get("order_price"),
                "sl": entry_ctx.get("sl"),
                "tp1": entry_ctx.get("tp1"),
                "notes": " ".join(eval_notes)
            })

        # Calculate Compliance Score
        total_trades = len(session_trades_json)
        if total_trades > 0:
            penalty = len(violations) * (1.0 / (total_trades * 2.0))
            compliance_score = max(0.0, min(1.0, 1.0 - penalty))
        else:
            compliance_score = 1.0

        # Plan Critique based on session dynamics
        plan_critique = {
            "regime_accuracy": (
                "Phù hợp với diễn biến phiên." if losses == 0 else 
                f"Chế độ {regime} có dấu hiệu biến động mạnh hơn dự kiến khi xuất hiện {losses} lệnh cắt lỗ."
            ),
            "sr_zones_evaluation": (
                "Các vùng H1/M30 S/R phát huy tốt vai trò làm điểm tựa phản ứng giá."
                if scratches <= 1 else
                "Thị trường tích lũy giằng co quanh vùng S/R, cần nới rộng biên độ lọc nhiễu (sr_proximity_points)."
            ),
            "wholesale_engine_assessment": (
                "Khoảng cách LWP/LRP và tỷ lệ R:R >= 1.0 được đảm bảo tốt."
                if not any(v.get("rule") == "WHOLESALE_ENTRY_VIOLATED" for v in violations) else
                "Có lệnh vào đuổi giá ngoài vùng giá sỉ, cần siết chặt điều kiện khớp lệnh Limit."
            ),
            "win_rate": f"{(wins / total_trades * 100):.1f}%" if total_trades > 0 else "N/A",
            "scratch_rate": f"{(scratches / total_trades * 100):.1f}%" if total_trades > 0 else "N/A"
        }

        # Parameter Adjustments Recommendation
        param_adjustments = {
            "scratch_timeout_bars_1m": 6 if scratches > 2 else 5,
            "min_rr_ratio_part1": 1.0,
            "post_trade_cooldown_seconds": 240 if losses > 1 else 180
        }

        # Dynamic Actionable Lessons
        lessons = []
        if losses > 0:
            lessons.append(f"Trong chế độ {regime}, kiên nhẫn đợi nến nén (Stall) rõ ràng trên M1 trước khi kích hoạt lệnh nhằm giảm thiểu Stopout.")
        if scratches > 1:
            lessons.append("Khi giá tích lũy đi ngang quanh ngưỡng cản, ưu tiên bảo vệ vốn và không mở lại vị thế ở cùng một Anchor swing.")
        if any(v.get("rule") == "WHOLESALE_ENTRY_VIOLATED" for v in violations):
            lessons.append("Tuyệt đối không dùng lệnh Market khi giá đã vượt ngưỡng LWP; chỉ đặt Limit tại vùng giá sỉ Wholesale.")
        if not lessons:
            lessons = [
                f"Kế hoạch phiên {session_id} ({regime}) vận hành chuẩn xác theo Lance Beggs YTC framework.",
                "Duy trì tỷ lệ Risk 1% và quản lý lệnh 2 phần (T1 chốt lời, Part 2 dời BE trailing).",
                "Tiếp tục tuân thủ quy tắc 1 điểm vào duy nhất trên mỗi Swing Anchor."
            ]

        raw_analysis = f"Post-Session Hindsight Audit hoàn tất: {total_trades} lệnh được đánh giá. Điểm tuân thủ: {compliance_score*100:.1f}%. Vi phạm: {len(violations)}."

        return AuditReport(
            session_id=session_id,
            compliance_score=round(compliance_score, 2),
            rule_violations=violations,
            hindsight_optimal_trades=[],
            lessons_learned=lessons,
            parameter_adjustments_suggested=param_adjustments,
            plan_critique=plan_critique,
            trade_evaluations=trade_evaluations,
            raw_ai_analysis=raw_analysis
        )
