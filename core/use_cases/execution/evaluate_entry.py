"""
Evaluate Entry Use Case (Server A Execution Engine)
"""
from typing import Dict, Any, List, Optional
import time
import asyncio
from core.domain.models import (
    Bar, SwingNode, SessionConfig, OrderSide, SetupType, 
    WholesaleCalculation, TradeLifecycle, PositionPart, PositionState, PreEntryEvaluation
)
from core.domain.rules.swing_detector import SwingDetector
from core.domain.rules.vector_dynamics import MicroPatternDetector, VectorDynamicsCalculator
from core.domain.rules.wholesale_engine import WholesaleEngine
from core.domain.rules.risk_manager import RiskManager
from core.domain.rules.setups.base import BaseSetup
from core.domain.rules.setups.setups import TSTSetup, BOFSetup, BPBSetup, PBSetup, CPBSetup
from core.domain.rules.setups.candlestick_setups import (
    TrendBarFailSetup, InsideBarSMA21Setup, IDNR4Setup, NR7EMA20Setup, YumYumSetup
)
from core.domain.rules.candlestick_engine import CandlestickEngine
from core.domain.rules.pre_entry_scorer import PreEntryScorer, DeterministicEvalResult
from core.domain.rules.lessons_compiler import LessonRule, LessonsCompiler
from core.domain.interfaces.broker import IBrokerGateway
from core.domain.interfaces.event_bus import IEventBus
from config import CONFIG


class EvaluateEntryUseCase:
    def __init__(
        self,
        broker: IBrokerGateway,
        event_bus: IEventBus,
        ai_engine: Optional[Any] = None,
        compiled_lesson_rules: Optional[List[LessonRule]] = None
    ):
        self.broker = broker
        self.event_bus = event_bus
        self.ai_engine = ai_engine
        self.setups = {
            "TST": TSTSetup(),
            "BOF": BOFSetup(),
            "BPB": BPBSetup(),
            "PB": PBSetup(),
            "CPB": CPBSetup(),
            # Price Action Vol 5 Setups
            "TREND_BAR_FAIL": TrendBarFailSetup(),
            "INSIDE_BAR_SMA21": InsideBarSMA21Setup(),
            "ID_NR4": IDNR4Setup(),
            "NR7_EMA20": NR7EMA20Setup(),
            "YUM_YUM": YumYumSetup()
        }
        self.consumed_anchors: set = set()
        self.stopped_out_anchors: set = set()
        self.stopped_out_spatial_keys: set = set()
        self.last_trade_closed_time: float = 0.0
        self.last_stopped_out_time: float = 0.0
        self.last_closed_state: Optional[PositionState] = None
        self.zone_scratch_history: Dict[str, List[float]] = {}
        self.last_trade_was_profitable: bool = False
        self.last_closed_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.total_session_trades: int = 0
        self.candidate_setup: Optional[Dict[str, Any]] = None
        # Layer 2: Compiled lesson rules — loaded once at startup, updated after each audit
        if compiled_lesson_rules is not None:
            self.compiled_lesson_rules = compiled_lesson_rules
        else:
            self.compiled_lesson_rules = []
            self._load_persisted_lesson_rules()

    def _load_persisted_lesson_rules(self) -> None:
        """Nạp lesson rules đã lưu từ JSON store vào memory — chạy 1 lần khi khởi động."""
        try:
            from infrastructure.storage.json_lesson_rules import JsonLessonRulesStore
            store = JsonLessonRulesStore()
            self.compiled_lesson_rules = store.load()
            if self.compiled_lesson_rules:
                print(f"[ENGINE] Loaded {len(self.compiled_lesson_rules)} lesson rules for Layer 2 gate.")
        except Exception as e:
            print(f"[ENGINE] Could not load lesson rules: {e}. Layer 2 will run without lessons.")
            self.compiled_lesson_rules = []

    def update_lesson_rules(self, lesson_rules: List[LessonRule]) -> None:
        """
        Cập nhật lesson rules sau khi audit xong.
        Gọi từ HindsightAuditorUseCase sau khi nhận AuditReport.
        """
        self.compiled_lesson_rules = lesson_rules
        print(f"[ENGINE] Layer 2 lesson rules updated: {len(lesson_rules)} rules active.")

    def record_trade_closed(self, trade: TradeLifecycle, is_loss: Optional[bool] = None):
        """Records closed trade outcome to adjust circuit breaker, dynamic cooldown, and zone scratch tracking."""
        self.last_trade_closed_time = trade.close_time or time.time()
        self.last_closed_state = trade.state
        self.last_closed_pnl = getattr(trade, "total_pnl", 0.0)

        # Check if trade closed with positive profit (either FULLY_CLOSED, profit-scratch, or partial lock)
        close_ctx_pnl = getattr(trade, "close_context", {}).get("total_pnl", 0.0) if getattr(trade, "close_context", None) else 0.0
        is_profitable = (self.last_closed_pnl > 0.0) or (close_ctx_pnl > 0.0)
        self.last_trade_was_profitable = is_profitable

        if is_loss is None:
            is_loss = (trade.state == PositionState.STOPPED_OUT) and not is_profitable
        
        if is_loss:
            self.consecutive_losses += 1
            self.last_stopped_out_time = self.last_trade_closed_time
            if trade.anchor_id:
                self.stopped_out_anchors.add(trade.anchor_id)
            if getattr(trade, "spatial_anchor_key", None):
                self.stopped_out_spatial_keys.add(trade.spatial_anchor_key)
        elif is_profitable or trade.state == PositionState.FULLY_CLOSED:
            # Winning trades reset consecutive losses immediately
            self.consecutive_losses = 0
            # If trade closed early but achieved profit, release anchor to allow continuation entries if setup re-triggers
            if is_profitable and trade.anchor_id:
                self.consumed_anchors.discard(trade.anchor_id)

        # Track scratches per spatial price zone ONLY for real zero/flat scratches (consolidation chop).
        # Profitable scratches (e.g. Profit Secured scratch) achieved profit and must NOT lockout the zone!
        if trade.state == PositionState.SCRATCHED and not is_profitable and getattr(trade, "spatial_anchor_key", None):
            now = self.last_trade_closed_time
            if trade.spatial_anchor_key not in self.zone_scratch_history:
                self.zone_scratch_history[trade.spatial_anchor_key] = []
            self.zone_scratch_history[trade.spatial_anchor_key].append(now)

    def reset_session(self):
        """Resets session tracking statistics."""
        self.consumed_anchors.clear()
        self.stopped_out_anchors.clear()
        self.stopped_out_spatial_keys.clear()
        self.zone_scratch_history.clear()
        self.last_trade_closed_time = 0.0
        self.last_stopped_out_time = 0.0
        self.last_closed_state = None
        self.last_closed_pnl = 0.0
        self.last_trade_was_profitable = False
        self.consecutive_losses = 0
        self.total_session_trades = 0
        self.candidate_setup = None


    async def execute(
        self,
        config: SessionConfig,
        bars_m30: Optional[List[Bar]] = None,
        bars_m3: Optional[List[Bar]] = None,
        bars_m1: Optional[List[Bar]] = None,
        active_trades: Optional[List[TradeLifecycle]] = None,
        bars_m15: Optional[List[Bar]] = None,
        bars_htf: Optional[List[Bar]] = None
    ) -> Optional[TradeLifecycle]:
        bars_htf = bars_htf or bars_m15 or bars_m30 or []
        bars_m3 = bars_m3 or []
        bars_m1 = bars_m1 or []
        active_trades = active_trades or []
        """
        Scans for valid setups matching current session config and triggers wholesale entry.
        Optimized for Live MT5 execution with strict capital protection constraints:
          1. 1 Entry Point per Swing Anchor (no repeat entries on oscillating swings).
          2. Post-trade Cooldown (default 180s pause after trade close).
          3. Max Consecutive Losses Circuit Breaker (pauses after 2 consecutive losses).
          4. Max Session Trades Cap (protects session capital).
        """
        # 1. Do not enter if there is already an active trade for this symbol
        if any(t.state in [PositionState.IN_POSITION, PositionState.PENDING_ENTRY, PositionState.TRAILING_STOP] for t in active_trades):
            return None

        if not bars_m1 or not bars_m3:
            return None

        risk_mgmt = config.risk_management or {}
        exec_rules = config.execution_rules or {}

        # 2. Session Transition & Strict Plan Verification Check
        if exec_rules.get("enable_session_transition_guard", True):
            from core.domain.rules.session_manager import SessionManager
            sm = getattr(self, "session_manager", None)
            if not sm:
                sm = SessionManager()
                self.session_manager = sm

            session_status = sm.get_session_status(config)
            if session_status.in_transition:
                return None
            if not session_status.is_plan_loaded:
                await self.event_bus.publish("telemetry", {
                    "type": "WAITING_FOR_SESSION_PLAN",
                    "session_tag": session_status.session_tag,
                    "reason": f"Plan for active session {session_status.current_session.value} not yet verified/loaded."
                })
                return None

        # 3. Capital Protection Constraint: Post-Trade Cooldown (Differentiated by outcome)
        base_cooldown = exec_rules.get("post_trade_cooldown_seconds", 180)
        profit_cooldown = exec_rules.get("profitable_close_cooldown_seconds", 0)

        if getattr(self, "last_trade_was_profitable", False) or (getattr(self, "last_closed_pnl", 0.0) > 0.0):
            # Trades closed early or fully with profit are allowed to continue trading immediately
            cooldown_secs = profit_cooldown
        elif self.last_closed_state == PositionState.SCRATCHED:
            cooldown_secs = exec_rules.get("scratch_cooldown_seconds", max(base_cooldown, 300))
        elif self.last_closed_state == PositionState.STOPPED_OUT:
            cooldown_secs = exec_rules.get("stopout_cooldown_seconds", max(base_cooldown, 420))
        else:
            cooldown_secs = base_cooldown

        now = time.time()
        if self.last_trade_closed_time > 0 and (now - self.last_trade_closed_time) < cooldown_secs:
            return None

        # 3a. News Blackout Window Filter (Freeze new entries around high/medium impact economic news)
        news_filter = config.news_filter or {}
        blackout_windows = news_filter.get("blackout_windows", [])
        if not blackout_windows:
            if not hasattr(self, "news_analyzer"):
                from core.use_cases.intelligence.news_sentiment_analyzer import NewsSentimentAnalyzerUseCase
                self.news_analyzer = NewsSentimentAnalyzerUseCase(ai_engine=self.ai_engine)
            blackout_st = self.news_analyzer.check_blackout_status(now)
            if blackout_st.get("is_in_blackout"):
                await self.event_bus.publish("telemetry", {
                    "type": "ENTRY_REJECTED",
                    "reason": f"News Blackout Active: Tạm ngừng giao dịch trước/sau tin {blackout_st.get('reason')}"
                })
                return None
            blackout_windows = blackout_st.get("blackout_windows", [])

        for bw in blackout_windows:
            if bw.get("start_ts", 0) <= now <= bw.get("end_ts", 0):
                event_title = bw.get("title", "High-Impact Economic Release")
                impact_label = bw.get("impact", "HIGH")
                await self.event_bus.publish("telemetry", {
                    "type": "ENTRY_REJECTED",
                    "reason": f"News Blackout Active: Tạm ngừng giao dịch trước/sau tin {impact_label} '{event_title}' ({bw.get('start_str')} -> {bw.get('end_str')})"
                })
                print(f"[ENGINE NEWS BLACKOUT] Entry REJECTED due to {impact_label} news: {event_title} ({bw.get('start_str')} -> {bw.get('end_str')})")
                return None

        # 4. Capital Protection Constraint: Max Consecutive Losses Circuit Breaker
        max_consecutive_losses = risk_mgmt.get("max_consecutive_losses", 2)
        if self.consecutive_losses >= max_consecutive_losses:
            await self.event_bus.publish("telemetry", {
                "type": "CIRCUIT_BREAKER_ACTIVE",
                "consecutive_losses": self.consecutive_losses,
                "reason": f"Circuit breaker engaged: {self.consecutive_losses} consecutive losses. Trading paused."
            })
            return None

        # 4. Capital Protection Constraint: Max Session Trades
        max_session_trades = risk_mgmt.get("max_session_trades", 6)
        if self.total_session_trades >= max_session_trades:
            return None

        # 5. Detect swings and trend on TTF (M3)
        swings_3m = SwingDetector.detect_swings(bars_m3)
        current_price = bars_m1[-1].close
        trend = SwingDetector.evaluate_trend(swings_3m, current_price)

        from core.domain.models import get_instrument_profile
        profile = get_instrument_profile(config.symbol)

        # Real-time Spread Filter (Blocks trades during news/illiquidity spread widening)
        try:
            sym_info = await self.broker.get_symbol_info(config.symbol)
            if sym_info and "ask" in sym_info and "bid" in sym_info:
                curr_spread = round(sym_info["ask"] - sym_info["bid"], profile.digits)
                max_spread = exec_rules.get("max_spread_points", profile.max_spread_points)
                if curr_spread > max_spread:
                    await self.event_bus.publish("telemetry", {
                        "type": "ENTRY_REJECTED",
                        "reason": f"Spread too high: {curr_spread} > {max_spread}. Protected from spread slippage."
                    })
                    return None
        except Exception:
            pass

        # 6. Detect 1m Stall Micro-structure (flexible fallback)
        is_stall, stall_low, stall_high = MicroPatternDetector.detect_stall(bars_m1, min_candles=3, atr_factor=1.5)
        if stall_low is None or stall_high is None:
            recent_m1 = bars_m1[-3:] if len(bars_m1) >= 3 else bars_m1
            stall_low = min(b.low for b in recent_m1)
            stall_high = max(b.high for b in recent_m1)

        # 6b. Check existing candidate setup awaiting entry touch
        if self.candidate_setup is not None:
            cand = self.candidate_setup
            if cand["anchor_id"] in self.consumed_anchors or cand["spatial_anchor_key"] in self.stopped_out_spatial_keys:
                self.candidate_setup = None
            else:
                curr_bar = bars_m1[-1]
                if cand.get("last_bar_timestamp") != curr_bar.timestamp:
                    cand["last_bar_timestamp"] = curr_bar.timestamp
                    cand["bars_waiting"] = cand.get("bars_waiting", 0) + 1

                max_pending = cand.get("max_bars_pending", 4)
                if max_pending and cand["bars_waiting"] >= max_pending:
                    print(f"[ENGINE] Candidate setup {cand['setup_name']} expired after {cand['bars_waiting']} bars. Dropping candidate.")
                    await self.event_bus.publish("telemetry", {
                        "type": "CANDIDATE_EXPIRED",
                        "setup": cand["setup_name"],
                        "reason": f"Hết hạn chờ ({cand['bars_waiting']} nến M1 mà giá không chạm entry)."
                    })
                    self.candidate_setup = None
                else:
                    # Check SL violation
                    sl_violated = (cand["side"] == OrderSide.BUY and current_price <= cand["sl"]) or \
                                  (cand["side"] == OrderSide.SELL and current_price >= cand["sl"])
                    if sl_violated:
                        print(f"[ENGINE] Candidate setup {cand['setup_name']} SL violated before touch. Dropping candidate.")
                    else:
                        atr_1m = MicroPatternDetector.calculate_atr(bars_m1, period=14)
                        touch_tolerance = max(profile.min_buffer_points, min(atr_1m * 0.35, profile.min_buffer_points * 1.5))
                        is_touched = (current_price <= cand["entry_price"] + touch_tolerance) if cand["side"] == OrderSide.BUY else (current_price >= cand["entry_price"] - touch_tolerance)
                        if is_touched:
                            return await self._execute_market_trade(
                                config=config,
                                setup_name=cand["setup_name"],
                                side=cand["side"],
                                current_price=current_price,
                                actual_sl=cand["sl"],
                                actual_tp1=cand["tp1"],
                                actual_tp2=cand["tp2"],
                                lot_total=cand["lot_total"],
                                lot_p1=cand["lot_p1"],
                                lot_p2=cand["lot_p2"],
                                wholesale=cand["wholesale"],
                                anchor_id=cand["anchor_id"],
                                spatial_anchor_key=cand["spatial_anchor_key"],
                                stall_low=cand["stall_low"],
                                stall_high=cand["stall_high"],
                                trend=cand["trend"],
                                bars_m1=bars_m1,
                                bars_m3=bars_m3,
                                risk_limit=cand["risk_limit"],
                                profile=profile,
                                macro_bias=cand.get("macro_bias", "NEUTRAL")
                            )
                        else:
                            # Still approaching entry, keep waiting
                            return None

        # 7. Check enabled setups according to session config
        for setup_name, enabled in config.setups_enabled.items():
            if not enabled:
                continue

            strategy = self.setups.get(setup_name)
            if not strategy:
                continue

            triggered, side, pullback_price, t1, t2 = strategy.evaluate(
                trend=trend,
                swings_3m=swings_3m,
                bars_1m=bars_m1,
                resistance_zones=config.resistance_zones,
                support_zones=config.support_zones,
                profile=profile
            )

            if triggered and side and pullback_price and t1 and t2:
                # 7a. Regime & Trend Gating Matrix (Anti-counter-trend protection)
                is_compat, compat_msg = BaseSetup.is_setup_compatible(
                    setup_type=SetupType(setup_name),
                    side=side,
                    market_regime=config.market_regime,
                    trend=trend.value if hasattr(trend, "value") else str(trend)
                )
                if not is_compat:
                    await self.event_bus.publish("telemetry", {
                        "type": "SETUP_REJECTED",
                        "setup": setup_name,
                        "reason": compat_msg
                    })
                    continue

                # 7b. Macro News Bias Filter (Avoid swimming against strong fundamental sentiment)
                macro_bias = str(news_filter.get("macro_bias", "NEUTRAL")).upper()
                if "BULLISH" in macro_bias and side == OrderSide.SELL:
                    await self.event_bus.publish("telemetry", {
                        "type": "SETUP_REJECTED",
                        "setup": setup_name,
                        "reason": f"Macro News Bias is {macro_bias}. Counter-macro SELL prohibited."
                    })
                    continue
                elif "BEARISH" in macro_bias and side == OrderSide.BUY:
                    await self.event_bus.publish("telemetry", {
                        "type": "SETUP_REJECTED",
                        "setup": setup_name,
                        "reason": f"Macro News Bias is {macro_bias}. Counter-macro BUY prohibited."
                    })
                    continue

                # 7c. Anti-Congestion Filter (Choppy sideways noise filter)
                if setup_name in ["PB", "CPB", "BPB", "YUM_YUM"] and CandlestickEngine.is_congestion(bars_m1, lookback=5):
                    await self.event_bus.publish("telemetry", {
                        "type": "SETUP_REJECTED",
                        "setup": setup_name,
                        "reason": f"Anti-Congestion Filter: Thị trường M1 đang giằng co nhiều râu nến (Congestion). Lọc bỏ setup tiếp diễn {setup_name} không chắc chắn."
                    })
                    continue

                # Spatial Anchor Deduplication & Max Retries per Zone Check
                spatial_anchor_key = f"{setup_name}_{side.value}_{round(pullback_price, 1)}"
                max_retries = exec_rules.get("max_retries_per_zone", 2)
                retry_window = exec_rules.get("zone_retry_window_seconds", 1800)
                recent_scratches = [
                    t for t in self.zone_scratch_history.get(spatial_anchor_key, [])
                    if now - t < retry_window
                ]
                if len(recent_scratches) >= max_retries:
                    # Zone locked out due to repeat scratches in consolidation!
                    continue

                # 8. Single Entry Anchor: 1 Swing structure = 1 Trade only
                latest_swing_time = int(swings_3m[-1].time) if swings_3m else 0
                anchor_id = f"{setup_name}_{side.value}_{round(pullback_price, 2)}_{latest_swing_time}"

                if anchor_id in self.consumed_anchors:
                    # Anchor already traded! Prevents oscillating re-entries around entry price.
                    continue

                # 8a. Anti-Revenge Lockout: Never re-enter an anchor or zone that was STOPPED_OUT
                if anchor_id in self.stopped_out_anchors or spatial_anchor_key in self.stopped_out_spatial_keys:
                    await self.event_bus.publish("telemetry", {
                        "type": "SETUP_REJECTED",
                        "setup": setup_name,
                        "reason": f"Anchor {spatial_anchor_key} previously stopped out. Revenge trading prohibited."
                    })
                    continue

                # 5. Calculate Wholesale Levels with Dynamic SL Floor (S1, LWP, LRP)
                atr_1m = MicroPatternDetector.calculate_atr(bars_m1, period=14)
                min_sl_dist = max(profile.min_sl_points, atr_1m * 1.8)

                sl_mult = float(risk_mgmt.get("sl_multiplier", getattr(CONFIG.risk, "SL_MULTIPLIER", 1.20)))
                tp_mult = float(risk_mgmt.get("tp_multiplier", getattr(CONFIG.risk, "TP_MULTIPLIER", 0.90)))
                min_rr = float(risk_mgmt.get("min_rr_ratio", getattr(CONFIG.risk, "MIN_RR_RATIO_PART1", 0.75)))
                min_profit_dist = getattr(profile, "min_profit_points", 2.0)

                # Tính biên độ nến gần nhất để SL neo ngoài vùng dao động tự nhiên của nến tín hiệu
                candle_buf_ratio = float(risk_mgmt.get("candle_buffer_ratio",
                    getattr(profile, "candle_buffer_ratio",
                    getattr(CONFIG.risk, "CANDLE_BUFFER_RATIO", 0.25))))
                candle_sl_mult = float(risk_mgmt.get("candle_sl_multiplier",
                    getattr(profile, "candle_sl_multiplier",
                    getattr(CONFIG.risk, "CANDLE_SL_MULTIPLIER", 1.20))))

                recent_bars_for_sl = bars_m1[-2:] if len(bars_m1) >= 2 else bars_m1
                recent_candle_range = max((b.high - b.low) for b in recent_bars_for_sl) if recent_bars_for_sl else 0.0
                recent_candle_low = min(b.low for b in recent_bars_for_sl) if recent_bars_for_sl else None
                recent_candle_high = max(b.high for b in recent_bars_for_sl) if recent_bars_for_sl else None

                # Cập nhật sàn SL kết hợp cả ATR và biên độ nến thực tế
                candle_sl_floor = recent_candle_range * candle_sl_mult
                min_sl_dist = max(min_sl_dist, candle_sl_floor)

                wholesale = WholesaleEngine.calculate_wholesale_levels(
                    setup_type=SetupType(setup_name),
                    side=side,
                    pullback_swing_price=pullback_price,
                    t1_price=t1,
                    t2_price=t2,
                    micro_stall_high=stall_high,
                    micro_stall_low=stall_low,
                    buffer_pts=profile.min_buffer_points,
                    min_sl_distance=min_sl_dist,
                    min_rr_ratio=min_rr,
                    sl_multiplier=sl_mult,
                    tp_multiplier=tp_mult,
                    adaptive_entry=(setup_name in ["INSIDE_BAR_SMA21", "TREND_BAR_FAIL", "NR7_EMA20", "YUM_YUM"] or exec_rules.get("enable_adaptive_wholesale_entry", False)),
                    min_profit_distance=min_profit_dist,
                    recent_candle_range=recent_candle_range,
                    recent_candle_low=recent_candle_low,
                    recent_candle_high=recent_candle_high,
                    candle_buffer_ratio=candle_buf_ratio
                )

                if not wholesale.is_valid_entry:
                    await self.event_bus.publish("telemetry", {
                        "type": "SETUP_REJECTED",
                        "setup": setup_name,
                        "reason": f"Entry price {wholesale.recommended_entry} fails Wholesale / R:R >= {min_rr:.2f} boundary (Min SL Floor {min_sl_dist:.2f})"
                    })
                    continue

                # 6. Calculate Position Sizing
                balance = await self.broker.get_account_balance()
                risk_mgmt = config.risk_management or {}
                fixed_lot = risk_mgmt.get("fixed_lot_size")
                risk_limit = risk_mgmt.get("account_risk_limit_percent", 1.0)

                # Determine Entry, SL, TP (Allowing manual override if configured, else auto-suggested)
                actual_sl = risk_mgmt.get("manual_sl") if risk_mgmt.get("manual_sl") is not None else wholesale.S1
                actual_tp1 = risk_mgmt.get("manual_tp1") if risk_mgmt.get("manual_tp1") is not None else wholesale.T1
                actual_tp2 = risk_mgmt.get("manual_tp2") if risk_mgmt.get("manual_tp2") is not None else wholesale.T2

                if fixed_lot and float(fixed_lot) > 0:
                    lot_total = float(fixed_lot)
                    if lot_total < 0.02:
                        lot_p1 = lot_total
                        lot_p2 = 0.0
                    else:
                        lot_p1 = round(lot_total * 0.5, 2)
                        lot_p2 = round(lot_total - lot_p1, 2)
                else:
                    lot_total, lot_p1, lot_p2 = RiskManager.calculate_lot_size(
                        balance=balance,
                        risk_percent=risk_limit,
                        entry_price=wholesale.recommended_entry,
                        sl_price=actual_sl,
                        point_size=profile.point,
                        tick_value=profile.tick_value
                    )

                # 6b. Apply Dynamic Lot Sizing Modifier from Macro News Filter
                lot_multiplier = float(news_filter.get("lot_multiplier", 1.0))
                if lot_multiplier <= 0.0:
                    await self.event_bus.publish("telemetry", {
                        "type": "SETUP_REJECTED",
                        "setup": setup_name,
                        "reason": "News Filter: Trading lot multiplier is 0.0 (High risk freeze)"
                    })
                    continue
                elif lot_multiplier < 1.0:
                    lot_total = max(round(lot_total * lot_multiplier, 2), 0.01)
                    if lot_total < 0.02:
                        lot_p1 = lot_total
                        lot_p2 = 0.0
                    else:
                        lot_p1 = round(lot_total * 0.5, 2)
                        lot_p2 = round(lot_total - lot_p1, 2)

                if lot_total <= 0:
                    continue

                # 7. Check if price is currently touching entry zone (using adaptive touch tolerance)
                touch_tolerance = max(profile.min_buffer_points, min(atr_1m * 0.35, profile.min_buffer_points * 1.5))
                is_touched = (current_price <= wholesale.recommended_entry + touch_tolerance) if side == OrderSide.BUY else (current_price >= wholesale.recommended_entry - touch_tolerance)

                # Guard: Do not enter if market price already invalidates SL
                if side == OrderSide.BUY and current_price <= actual_sl:
                    continue
                if side == OrderSide.SELL and current_price >= actual_sl:
                    continue

                if is_touched:
                    return await self._execute_market_trade(
                        config=config,
                        setup_name=setup_name,
                        side=side,
                        current_price=current_price,
                        actual_sl=actual_sl,
                        actual_tp1=actual_tp1,
                        actual_tp2=actual_tp2,
                        lot_total=lot_total,
                        lot_p1=lot_p1,
                        lot_p2=lot_p2,
                        wholesale=wholesale,
                        anchor_id=anchor_id,
                        spatial_anchor_key=spatial_anchor_key,
                        stall_low=stall_low,
                        stall_high=stall_high,
                        trend=trend,
                        bars_m1=bars_m1,
                        bars_m3=bars_m3,
                        risk_limit=risk_limit,
                        profile=profile,
                        macro_bias=macro_bias
                    )
                else:
                    # Approaching entry -> DO NOT PLACE LIMIT ORDER TO BROKER/MT5!
                    if setup_name == "TREND_BAR_FAIL":
                        max_bars_pending = 3
                    elif setup_name in ["YUM_YUM", "INSIDE_BAR_SMA21", "ID_NR4", "NR7_EMA20"]:
                        max_bars_pending = 5
                    else:
                        max_bars_pending = 5

                    self.candidate_setup = {
                        "symbol": config.symbol,
                        "session_id": config.session_id,
                        "session_tag": config.session_tag,
                        "market_regime": config.market_regime,
                        "setup_name": setup_name,
                        "side": side,
                        "entry_price": wholesale.recommended_entry,
                        "sl": actual_sl,
                        "tp1": actual_tp1,
                        "tp2": actual_tp2,
                        "lot_total": lot_total,
                        "lot_p1": lot_p1,
                        "lot_p2": lot_p2,
                        "wholesale": wholesale,
                        "anchor_id": anchor_id,
                        "spatial_anchor_key": spatial_anchor_key,
                        "stall_low": stall_low,
                        "stall_high": stall_high,
                        "trend": trend.value if hasattr(trend, "value") else str(trend),
                        "max_bars_pending": max_bars_pending,
                        "bars_waiting": 0,
                        "last_bar_timestamp": bars_m1[-1].timestamp if bars_m1 else time.time(),
                        "risk_limit": risk_limit,
                        "macro_bias": macro_bias,
                        "created_time": time.time()
                    }
                    dist_pts = round(abs(current_price - wholesale.recommended_entry), 2)
                    await self.event_bus.publish("telemetry", {
                        "type": "CANDIDATE_SETUP_DETECTED",
                        "symbol": config.symbol,
                        "setup": setup_name,
                        "side": side.value,
                        "entry": wholesale.recommended_entry,
                        "current_price": current_price,
                        "dist": dist_pts,
                        "sl": actual_sl,
                        "tp1": actual_tp1,
                        "message": f"⏳ Phát hiện setup {setup_name} {side.value} tại {wholesale.recommended_entry:.2f}. Máy chủ đang rình chờ giá chạm entry để bắn lệnh thị trường (cách {dist_pts:.2f} pts)..."
                    })
                    print(f"[ENGINE] Candidate Setup Detected: {setup_name} {side.value} at {wholesale.recommended_entry} (Current: {current_price}, dist: {dist_pts}). Watching for touch...")
                    return None

        return None

    async def _execute_market_trade(
        self,
        config: SessionConfig,
        setup_name: str,
        side: OrderSide,
        current_price: float,
        actual_sl: float,
        actual_tp1: float,
        actual_tp2: float,
        lot_total: float,
        lot_p1: float,
        lot_p2: float,
        wholesale: Any,
        anchor_id: str,
        spatial_anchor_key: str,
        stall_low: float,
        stall_high: float,
        trend: Any,
        bars_m1: List[Bar],
        bars_m3: List[Bar],
        risk_limit: float,
        profile: Any,
        macro_bias: str = "NEUTRAL"
    ) -> Optional[TradeLifecycle]:
        risk_mgmt = config.risk_management or {}
        exec_rules = config.execution_rules or {}
        order_price = current_price
        ws_dict = wholesale.__dict__ if hasattr(wholesale, "__dict__") else wholesale

        # 8. Pre-Entry Validation: 3-LAYER GATE
        enable_ai_pre_entry = exec_rules.get("enable_ai_pre_entry", risk_mgmt.get("enable_ai_pre_entry", True))
        min_ai_confidence = float(exec_rules.get("min_ai_confidence", risk_mgmt.get("min_ai_confidence", 0.75)))
        layer2_threshold = float(exec_rules.get("layer2_threshold", 0.80))
        ai_eval = None

        # ------ LAYER 2: Deterministic Scorer (0 tokens) ------
        det_result = PreEntryScorer.evaluate(
            setup=setup_name,
            side=side.value,
            regime=config.market_regime.value,
            wholesale=ws_dict,
            macro_bias=macro_bias,
            profile=profile,
            lesson_rules=self.compiled_lesson_rules,
            threshold=layer2_threshold,
        )

        score_log = PreEntryScorer.score_summary(det_result)
        print(f"[LAYER2] {setup_name} {side.value}: {score_log}")

        if not det_result.approved:
            await self.event_bus.publish("telemetry", {
                "type": "LAYER2_REJECTED",
                "setup": setup_name,
                "side": side.value,
                "price": order_price,
                "det_score": det_result.score,
                "reason": det_result.rejection_reason,
                "triggered_rules": det_result.triggered_lesson_rules,
                "message": (
                    f"⚡ [L2] Từ chối {setup_name} {side.value} (score={det_result.score:.2f}): "
                    f"{det_result.rejection_reason}"
                )
            })
            self.candidate_setup = None
            return None

        # ------ LAYER 3: AI Deep Validation (compact prompt, chỉ khi L2 pass) ------
        if enable_ai_pre_entry and self.ai_engine:
            await self.event_bus.publish("telemetry", {
                "type": "AI_PRE_ENTRY_EVALUATING",
                "symbol": config.symbol,
                "setup": setup_name,
                "side": side.value,
                "entry": order_price,
                "det_score": det_result.score,
                "model": getattr(self.ai_engine, "model_name", "AI"),
                "message": (
                    f"🤖 [L3] AI đánh giá {setup_name} {side.value} tại {order_price} "
                    f"(L2 score={det_result.score:.2f})..."
                )
            })

            candidate_ctx = {
                "symbol": config.symbol,
                "setup": setup_name,
                "side": side.value,
                "order_type": "MARKET",
                "order_price": order_price,
                "sl": actual_sl,
                "tp1": actual_tp1,
                "tp2": actual_tp2,
                "wholesale": ws_dict,
                "stall_range": {"low": stall_low, "high": stall_high},
                "det_score": round(det_result.score, 3),
                "nearest_zones": [
                    {"id": z.id, "type": getattr(z, "zone_type", "S/R"), "high": z.high, "low": z.low}
                    for z in (config.resistance_zones + config.support_zones)
                ]
            }

            trading_cfg_dict = {
                "symbol": config.symbol,
                "market_regime": config.market_regime.value,
                "setups_enabled": config.setups_enabled
            }

            recent_snapshot = {
                "m1_last_5": [{"open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in (bars_m1[-5:] if len(bars_m1) >= 5 else bars_m1)],
                "m3_last_3": [{"open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in (bars_m3[-3:] if len(bars_m3) >= 3 else bars_m3)]
            }

            try:
                ai_eval = await asyncio.wait_for(
                    self.ai_engine.evaluate_candidate_trade(candidate_ctx, trading_cfg_dict, recent_snapshot),
                    timeout=4.0
                )
            except asyncio.TimeoutError:
                print(f"[ENGINE] AI Pre-entry evaluation timed out (>4.0s) for {setup_name}.")
                fallback_policy = exec_rules.get("ai_timeout_policy", "REJECT")
                if fallback_policy == "REJECT":
                    await self.event_bus.publish("telemetry", {
                        "type": "AI_ENTRY_VETOED",
                        "symbol": config.symbol,
                        "setup": setup_name,
                        "side": side.value,
                        "entry": order_price,
                        "confidence": 0.0,
                        "reason": ">4s timeout (REJECT policy)",
                        "policy": fallback_policy,
                        "message": f"🚫 Điểm vào {setup_name} {side.value} bị hủy do AI phản hồi quá thời gian cho phép (>4s)."
                    })
                    self.candidate_setup = None
                    return None
            except Exception as e:
                print(f"[ENGINE] AI Pre-entry evaluation error: {e}")

            if ai_eval:
                if not ai_eval.approved or ai_eval.confidence < min_ai_confidence:
                    await self.event_bus.publish("telemetry", {
                        "type": "AI_ENTRY_VETOED",
                        "symbol": config.symbol,
                        "setup": setup_name,
                        "side": side.value,
                        "entry": order_price,
                        "confidence": ai_eval.confidence,
                        "det_score": det_result.score,
                        "reason": ai_eval.reason,
                        "concerns": ai_eval.concerns,
                        "message": f"🚫 AI TỪ CHỐI điểm vào {setup_name} {side.value}! Lý do: {ai_eval.reason} (Độ tin cậy: {ai_eval.confidence*100:.0f}%)"
                    })
                    print(f"[AI_GATEKEEPER] VETOED entry {setup_name} {side.value} at {order_price}: {ai_eval.reason}")
                    self.candidate_setup = None
                    return None
                else:
                    await self.event_bus.publish("telemetry", {
                        "type": "AI_ENTRY_APPROVED",
                        "symbol": config.symbol,
                        "setup": setup_name,
                        "side": side.value,
                        "entry": order_price,
                        "confidence": ai_eval.confidence,
                        "det_score": det_result.score,
                        "reason": ai_eval.reason,
                        "message": f"✅ AI PHÊ DUYỆT điểm vào {setup_name} {side.value}! (Độ tin cậy: {ai_eval.confidence*100:.0f}%) - {ai_eval.reason}"
                    })
                    print(f"[AI_GATEKEEPER] APPROVED entry {setup_name} {side.value} at {order_price} ({ai_eval.confidence*100:.0f}%): {ai_eval.reason}")

        trade_id = f"ytc_{int(time.time()*1000)}"

        # Dispatch MARKET orders directly to Broker
        if lot_p2 > 0:
            ticket_p1 = await self.broker.place_order(
                symbol=config.symbol,
                side=side,
                order_type="MARKET",
                volume=lot_p1,
                price=order_price,
                sl=actual_sl,
                tp=actual_tp1,
                comment=f"{trade_id}_P1_{setup_name}"
            )
            if not ticket_p1:
                await self.event_bus.publish("telemetry", {
                    "type": "ORDER_FAILED",
                    "setup": setup_name,
                    "reason": "Broker rejected Part 1 order (check MT5 terminal AlgoTrading status)"
                })
                self.candidate_setup = None
                return None

            ticket_p2 = await self.broker.place_order(
                symbol=config.symbol,
                side=side,
                order_type="MARKET",
                volume=lot_p2,
                price=order_price,
                sl=actual_sl,
                tp=actual_tp2,
                comment=f"{trade_id}_P2_{setup_name}"
            )
            if not ticket_p2:
                ticket_p2 = None
                lot_p2 = 0.0
                lot_total = lot_p1
        else:
            ticket_p1 = await self.broker.place_order(
                symbol=config.symbol,
                side=side,
                order_type="MARKET",
                volume=lot_total,
                price=order_price,
                sl=actual_sl,
                tp=actual_tp1,
                comment=f"{trade_id}_{setup_name}"
            )
            if not ticket_p1:
                await self.event_bus.publish("telemetry", {
                    "type": "ORDER_FAILED",
                    "setup": setup_name,
                    "reason": "Broker rejected order (check MT5 terminal AlgoTrading status)"
                })
                self.candidate_setup = None
                return None
            ticket_p2 = None

        self.consumed_anchors.add(anchor_id)
        self.total_session_trades += 1
        self.candidate_setup = None

        trend_str = trend.value if hasattr(trend, "value") else str(trend)
        entry_context = {
            "symbol": config.symbol,
            "session_id": config.session_id,
            "session_tag": config.session_tag,
            "market_regime": config.market_regime.value,
            "trend": trend_str,
            "setup": setup_name,
            "side": side.value,
            "order_type": "MARKET",
            "order_price": order_price,
            "sl": actual_sl,
            "tp1": actual_tp1,
            "tp2": actual_tp2,
            "wholesale": ws_dict,
            "stall_range": {"low": stall_low, "high": stall_high},
            "nearest_zones": [
                {"id": z.id, "type": getattr(z, "zone_type", "S/R"), "high": z.high, "low": z.low, "significance": z.significance.value if hasattr(z.significance, "value") else str(z.significance)}
                for z in (config.resistance_zones + config.support_zones)
            ],
            "total_volume": lot_total,
            "lots": {"total": lot_total, "p1": lot_p1, "p2": lot_p2},
            "risk_percent": risk_limit,
            "timestamp": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "ai_pre_evaluation": {
                "approved": ai_eval.approved,
                "confidence": ai_eval.confidence,
                "reason": ai_eval.reason,
                "model_name": ai_eval.model_name
            } if ai_eval else None
        }

        lifecycle = TradeLifecycle(
            trade_id=trade_id,
            symbol=config.symbol,
            setup_type=SetupType(setup_name),
            side=side,
            state=PositionState.IN_POSITION,
            part1=PositionPart(1, lot_p1, order_price, actual_sl, actual_tp1, ticket=ticket_p1),
            part2=PositionPart(2, lot_p2, order_price, actual_sl, actual_tp2, ticket=ticket_p2 or ticket_p1),
            open_time=time.time(),
            limit_order_ticket=None,
            stop_order_ticket=None,
            m1_bars_in_trade=0,
            last_bar_timestamp=bars_m1[-1].timestamp if bars_m1 else None,
            anchor_id=anchor_id,
            spatial_anchor_key=spatial_anchor_key,
            max_bars_pending=None,
            entry_context=entry_context
        )

        await self.event_bus.publish("trade_opened", {
            "trade_id": trade_id,
            "setup": setup_name,
            "side": side.value,
            "type": "MARKET",
            "ticket": ticket_p1,
            "ticket_p2": ticket_p2,
            "wholesale": ws_dict,
            "lots": {"total": lot_total, "p1": lot_p1, "p2": lot_p2},
            "anchor_id": anchor_id
        })

        print(f"[ENGINE] Trade Executed: {trade_id} [{setup_name} {side.value} MARKET ticket1={ticket_p1} ticket2={ticket_p2} anchor={anchor_id}]")
        return lifecycle
