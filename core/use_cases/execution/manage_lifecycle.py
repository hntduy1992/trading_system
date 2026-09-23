"""
Manage Position Lifecycle Use Case (Server A Execution Engine)
Implements Section 2.4 Active Lifecycle Management State Machine
"""
from typing import List, Optional
import time
from core.domain.models import (
    Bar, SwingNode, SwingType, TradeLifecycle, PositionState, OrderSide,
    calculate_pnl, get_instrument_profile
)
from core.domain.rules.risk_manager import RiskManager
from core.domain.rules.swing_detector import SwingDetector
from core.domain.rules.profit_protector import ProfitProtector
from core.domain.rules.candlestick_engine import CandlestickEngine
from infrastructure.storage.json_lesson_rules import JsonLessonRulesStore
from core.domain.interfaces.broker import IBrokerGateway
from core.domain.interfaces.event_bus import IEventBus
from config import CONFIG

class ManageLifecycleUseCase:
    def __init__(self, broker: IBrokerGateway, event_bus: IEventBus):
        self.broker = broker
        self.event_bus = event_bus

    async def update(
        self,
        trade: TradeLifecycle,
        bars_m1: List[Bar],
        bars_m3: List[Bar],
        scratch_timeout_bars: int = 8,
        min_holding_bars: int = 3
    ) -> TradeLifecycle:
        """
        Processes new bar events and transitions trade through lifecycle states.
        Safeguards:
          - Bars counted strictly on actual M1 bar timestamp changes (prevents 1s tick over-counting).
          - Minimum holding bars enforced before evaluating scratch rule.
        """
        if trade.state in [PositionState.FULLY_CLOSED, PositionState.SCRATCHED, PositionState.STOPPED_OUT]:
            return trade

        if not bars_m1:
            return trade

        curr_bar = bars_m1[-1]
        curr_price = curr_bar.close

        # 1. State: PENDING_ENTRY -> Check fill
        if trade.state == PositionState.PENDING_ENTRY:
            filled = False
            # First check if broker already reports this pending order as an open position
            if hasattr(self.broker, "get_open_positions"):
                try:
                    open_pos = await self.broker.get_open_positions(trade.symbol)
                    pos_tickets = {p["ticket"] for p in open_pos}
                    target_ticket = trade.limit_order_ticket or trade.stop_order_ticket or trade.part1.ticket
                    if target_ticket and target_ticket in pos_tickets:
                        filled = True
                except Exception:
                    pass

            # Fallback to bar price trigger if not confirmed yet (ONLY for PaperBroker / simulated broker)
            is_simulated = type(self.broker).__name__ in ["PaperBroker", "MockBroker", "MockTrackingBroker"] or not getattr(self.broker, "connected", True)
            if not filled and is_simulated:
                is_pure_stop = bool(trade.stop_order_ticket and not trade.limit_order_ticket)
                is_pure_limit = bool(trade.limit_order_ticket and not trade.stop_order_ticket)

                if trade.side == OrderSide.BUY:
                    if is_pure_stop:
                        if curr_bar.high >= trade.part1.entry_price:
                            filled = True
                    elif is_pure_limit:
                        if curr_bar.low <= trade.part1.entry_price:
                            filled = True
                    else:
                        if curr_bar.low <= trade.part1.entry_price:
                            filled = True
                            if trade.stop_order_ticket:
                                await self.broker.cancel_order(trade.stop_order_ticket)
                        elif curr_bar.high >= trade.part2.entry_price:
                            filled = True
                            if trade.limit_order_ticket:
                                await self.broker.cancel_order(trade.limit_order_ticket)
                else:
                    if is_pure_stop:
                        if curr_bar.low <= trade.part1.entry_price:
                            filled = True
                    elif is_pure_limit:
                        if curr_bar.high >= trade.part1.entry_price:
                            filled = True
                    else:
                        if curr_bar.high >= trade.part1.entry_price:
                            filled = True
                            if trade.stop_order_ticket:
                                await self.broker.cancel_order(trade.stop_order_ticket)
                        elif curr_bar.low <= trade.part2.entry_price:
                            filled = True
                            if trade.limit_order_ticket:
                                await self.broker.cancel_order(trade.limit_order_ticket)


            if filled:
                trade.state = PositionState.IN_POSITION
                trade.m1_bars_in_trade = 0
                trade.last_bar_timestamp = curr_bar.timestamp
                trade.initial_risk_dist = abs(trade.part1.entry_price - trade.part1.sl_price)
                await self.event_bus.publish("telemetry", {
                    "type": "ORDER_FILLED",
                    "trade_id": trade.trade_id,
                    "state": trade.state.value
                })
            else:
                # Check Time-In-Force pending order expiry (Vol 5: 1-bar or 3-bar timeout)
                if trade.last_bar_timestamp is None:
                    trade.last_bar_timestamp = curr_bar.timestamp
                    trade.m1_bars_in_trade = 0
                elif trade.last_bar_timestamp != curr_bar.timestamp:
                    trade.last_bar_timestamp = curr_bar.timestamp
                    trade.m1_bars_in_trade += 1

                max_pending = getattr(trade, "max_bars_pending", None)
                if max_pending is not None and trade.m1_bars_in_trade >= max_pending:
                    # Time-In-Force expired: Cancel pending order immediately
                    cancelled_tickets = []
                    if trade.limit_order_ticket:
                        await self.broker.cancel_order(trade.limit_order_ticket)
                        cancelled_tickets.append(trade.limit_order_ticket)
                    if trade.stop_order_ticket:
                        await self.broker.cancel_order(trade.stop_order_ticket)
                        cancelled_tickets.append(trade.stop_order_ticket)
                    if trade.part1.ticket and trade.part1.ticket not in cancelled_tickets:
                        await self.broker.cancel_order(trade.part1.ticket)
                        cancelled_tickets.append(trade.part1.ticket)

                    trade.state = PositionState.SCRATCHED
                    trade.close_time = time.time()
                    trade.part1.is_closed = True
                    trade.part2.is_closed = True
                    trade.close_context = {
                        "reason": f"Time-In-Force expired ({trade.m1_bars_in_trade} bars >= max {max_pending} bars)",
                        "bars_elapsed": trade.m1_bars_in_trade
                    }
                    await self.event_bus.publish("telemetry", {
                        "type": "PENDING_ORDER_EXPIRED",
                        "trade_id": trade.trade_id,
                        "setup": trade.setup_type.value if hasattr(trade.setup_type, "value") else str(trade.setup_type),
                        "bars_elapsed": trade.m1_bars_in_trade,
                        "max_bars": max_pending,
                        "message": f"⏳ Lệnh chờ {trade.trade_id} ({trade.setup_type.value}) tự động hủy do quá thời gian {max_pending} nến chưa khớp."
                    })
                    print(f"[ENGINE] Pending Order Expired & Cancelled: {trade.trade_id} after {trade.m1_bars_in_trade} bars (max {max_pending}).")

            return trade

        # 2. State: IN_POSITION
        # Strictly increment bar counter ONLY when a new M1 bar timestamp occurs
        if trade.last_bar_timestamp is None:
            trade.last_bar_timestamp = curr_bar.timestamp
            trade.m1_bars_in_trade = 1
        elif trade.last_bar_timestamp != curr_bar.timestamp:
            trade.last_bar_timestamp = curr_bar.timestamp
            trade.m1_bars_in_trade += 1

        if getattr(trade, "initial_risk_dist", None) is None or trade.initial_risk_dist <= 0:
            trade.initial_risk_dist = abs(trade.part1.entry_price - trade.part1.sl_price)

        # Check Stop Loss hit
        sl_hit = False
        if trade.side == OrderSide.BUY and curr_bar.low <= trade.part1.sl_price:
            sl_hit = True
        elif trade.side == OrderSide.SELL and curr_bar.high >= trade.part1.sl_price:
            sl_hit = True

        if sl_hit:
            trade.state = PositionState.STOPPED_OUT
            trade.close_time = time.time()
            profile = get_instrument_profile(trade.symbol)
            # Close active tickets on broker before marking parts as closed
            closed_tickets = set()
            for part in [trade.part1, trade.part2]:
                if part.ticket and part.ticket not in closed_tickets and not part.is_closed:
                    await self.broker.close_position(part.ticket)
                    closed_tickets.add(part.ticket)
                part.is_closed = True
                part.close_price = trade.part1.sl_price
                part.pnl = calculate_pnl(part.entry_price, trade.part1.sl_price, part.lot_size, trade.side, profile)

            trade.close_context = {
                "close_state": "STOPPED_OUT",
                "close_reason": "INITIAL_STOP_LOSS_HIT",
                "total_pnl": trade.total_pnl,
                "exit_price_part1": trade.part1.sl_price,
                "exit_price_part2": trade.part1.sl_price,
                "bars_in_trade": trade.m1_bars_in_trade,
                "close_time": trade.close_time,
                "close_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade.close_time))
            }
            await self.event_bus.publish("telemetry", {
                "type": "STOP_LOSS_HIT",
                "trade_id": trade.trade_id,
                "total_pnl": trade.total_pnl,
                "price": curr_price
            })
            return trade

        # Check T1 HIT Event
        t1_hit = False
        if trade.side == OrderSide.BUY and curr_bar.high >= trade.part1.tp_price:
            t1_hit = True
        elif trade.side == OrderSide.SELL and curr_bar.low <= trade.part1.tp_price:
            t1_hit = True

        if t1_hit and trade.state == PositionState.IN_POSITION:
            profile = get_instrument_profile(trade.symbol)
            # Liquidate Part 1
            trade.part1.is_closed = True
            trade.part1.close_price = trade.part1.tp_price
            trade.part1.pnl = calculate_pnl(trade.part1.entry_price, trade.part1.tp_price, trade.part1.lot_size, trade.side, profile)
            if trade.part1.ticket:
                await self.broker.close_position(trade.part1.ticket, trade.part1.lot_size)

            # Single position (e.g. 0.01 lot total, no Part 2): Full close on T1
            if trade.part2.lot_size <= 0 or not trade.part2.ticket:
                trade.state = PositionState.FULLY_CLOSED
                trade.close_time = time.time()
                trade.part2.is_closed = True
                trade.part2.close_price = trade.part1.tp_price
                trade.part2.pnl = 0.0
                trade.close_context = {
                    "close_state": "FULLY_CLOSED",
                    "close_reason": "T1_TARGET_HIT_SINGLE_POSITION",
                    "total_pnl": trade.part1.pnl,
                    "exit_price_part1": trade.part1.tp_price,
                    "exit_price_part2": trade.part1.tp_price,
                    "bars_in_trade": trade.m1_bars_in_trade,
                    "close_time": trade.close_time,
                    "close_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade.close_time))
                }
                await self.event_bus.publish("telemetry", {
                    "type": "T1_HIT_FULLY_CLOSED",
                    "trade_id": trade.trade_id,
                    "part1_pnl": trade.part1.pnl,
                    "total_pnl": trade.total_pnl,
                    "message": f"🎯 T1 Hit! Lệnh {trade.trade_id} đã chốt lời hoàn tất: {trade.part1.pnl:+.2f}$ (đạt tối thiểu >= 2.00$)."
                })
                print(f"[ENGINE] Single Position T1 Hit & Fully Closed: {trade.trade_id} PnL: {trade.part1.pnl:+.2f}$")
                return trade

            # Modify Part 2 SL to Breakeven (+/- be_buffer guaranteeing min profit >= $2.00) ONLY if enabled
            profit_cfg = getattr(CONFIG, "profit_protection", None)
            move_be_on_t1 = getattr(profit_cfg, "MOVE_SL_TO_BE_ON_T1", False)
            enable_sl_trailing = getattr(profit_cfg, "ENABLE_SL_TRAILING", getattr(getattr(CONFIG, "risk", None), "ENABLE_SL_TRAILING", False))

            if move_be_on_t1 or enable_sl_trailing:
                be_buffer = max(profile.be_buffer_points, getattr(profile, "min_profit_points", 2.0))
                new_sl = (trade.part2.entry_price + be_buffer) if trade.side == OrderSide.BUY else (trade.part2.entry_price - be_buffer)
                trade.part2.sl_price = round(new_sl, profile.digits)
                if trade.part2.ticket:
                    await self.broker.modify_position(trade.part2.ticket, sl=trade.part2.sl_price, tp=trade.part2.tp_price)
                sl_msg = f"Part 2 dời SL về {trade.part2.sl_price:.2f} (Khóa chắc lãi >= 2.00$)"
            else:
                sl_msg = f"Part 2 giữ nguyên SL {trade.part2.sl_price:.2f} (Quản lý thoát bằng tín hiệu nến Price Action)"

            trade.state = PositionState.TRAILING_STOP
            trade.bars_in_trailing = 0
            trade.last_trailing_bar_timestamp = curr_bar.timestamp
            trade.profit_protection_level = 0
            trade.max_unrealized_r_part2 = 0.0
            await self.event_bus.publish("telemetry", {
                "type": "T1_HIT",
                "trade_id": trade.trade_id,
                "part1_pnl": trade.part1.pnl,
                "new_sl": trade.part2.sl_price,
                "message": f"🎯 T1 Hit! Part 1 chốt lãi {trade.part1.pnl:+.2f}$, {sl_msg}."
            })
            return trade

        # Early Breakeven & Profit Lock in IN_POSITION (Locks profit safely before T1 only if enabled)
        profile = get_instrument_profile(trade.symbol)
        min_p_pts = getattr(profile, "min_profit_points", 2.0)
        be_cushion = getattr(profile, "be_buffer_points", 2.0)
        enable_early_lock = getattr(getattr(CONFIG, "risk", None), "ENABLE_EARLY_PROFIT_LOCK", False)
        enable_sl_trailing = getattr(getattr(CONFIG, "risk", None), "ENABLE_SL_TRAILING", False)
        if enable_early_lock and enable_sl_trailing and trade.state == PositionState.IN_POSITION and not getattr(trade, "early_profit_locked", False):
            # Require minimum buffer clearance between current price and new SL to avoid choking position
            min_clearance = 0.15
            if trade.side == OrderSide.BUY:
                curr_profit_pts = curr_bar.high - trade.part1.entry_price
                lock_sl = round(trade.part1.entry_price + be_cushion, profile.digits)
                can_lock = (curr_profit_pts >= min_p_pts) and (curr_price >= lock_sl + min_clearance)
            else:
                curr_profit_pts = trade.part1.entry_price - curr_bar.low
                lock_sl = round(trade.part1.entry_price - be_cushion, profile.digits)
                can_lock = (curr_profit_pts >= min_p_pts) and (curr_price <= lock_sl - min_clearance)

            if can_lock:
                if trade.side == OrderSide.BUY and lock_sl > trade.part1.sl_price:
                    trade.part1.sl_price = lock_sl
                    trade.part2.sl_price = lock_sl
                    trade.early_profit_locked = True
                    for part in [trade.part1, trade.part2]:
                        if part.ticket:
                            await self.broker.modify_position(part.ticket, sl=lock_sl, tp=part.tp_price)
                    await self.event_bus.publish("telemetry", {
                        "type": "EARLY_PROFIT_LOCKED",
                        "trade_id": trade.trade_id,
                        "new_sl": lock_sl,
                        "locked_profit_pts": be_cushion,
                        "message": f"🔒 Khóa lợi nhuận sớm: SL dời lên {lock_sl:.2f} (Khóa chắc lợi nhuận >= 2.00$). Không thể lỗ."
                    })
                elif trade.side == OrderSide.SELL and lock_sl < trade.part1.sl_price:
                    trade.part1.sl_price = lock_sl
                    trade.part2.sl_price = lock_sl
                    trade.early_profit_locked = True
                    for part in [trade.part1, trade.part2]:
                        if part.ticket:
                            await self.broker.modify_position(part.ticket, sl=lock_sl, tp=part.tp_price)
                    await self.event_bus.publish("telemetry", {
                        "type": "EARLY_PROFIT_LOCKED",
                        "trade_id": trade.trade_id,
                        "new_sl": lock_sl,
                        "locked_profit_pts": be_cushion,
                        "message": f"🔒 Khóa lợi nhuận sớm: SL dời xuống {lock_sl:.2f} (Khóa chắc lợi nhuận >= 2.00$). Không thể lỗ."
                    })

        # Candlestick Exit in IN_POSITION (Chốt vị thế chủ động nếu có mô hình nến đảo chiều KHI ĐÃ ĐẠT LỢI NHUẬN TỐI THIỂU >= $2 / 0.01 lot)
        enable_candlestick_exits = getattr(getattr(CONFIG, "profit_protection", None), "ENABLE_CANDLESTICK_EXITS", True)
        if enable_candlestick_exits and trade.state == PositionState.IN_POSITION and trade.m1_bars_in_trade >= 5:
            min_usd_required = 2.0 * (trade.total_volume / 0.01) if trade.total_volume > 0 else 2.0
            unrealized_pnl = calculate_pnl(trade.part1.entry_price, curr_price, trade.total_volume, trade.side, profile)
            if unrealized_pnl >= min_usd_required:
                should_candle_exit, candle_reason = CandlestickEngine.evaluate_candlestick_exit(
                    trade=trade,
                    bars_m1=bars_m1,
                    bars_m3=bars_m3,
                    min_r=0.5,
                    curr_price=curr_price
                )
                if should_candle_exit:
                    trade.state = PositionState.FULLY_CLOSED
                    trade.close_time = time.time()
                    profile = get_instrument_profile(trade.symbol)
                    closed_tickets = set()
                    for part in [trade.part1, trade.part2]:
                        if part.ticket and part.ticket not in closed_tickets and not part.is_closed:
                            await self.broker.close_position(part.ticket)
                            closed_tickets.add(part.ticket)
                        part.is_closed = True
                        part.close_price = curr_price
                        part.pnl = calculate_pnl(part.entry_price, curr_price, part.lot_size, trade.side, profile)

                    trade.close_context = {
                        "close_state": "FULLY_CLOSED",
                        "close_reason": candle_reason,
                        "total_pnl": trade.total_pnl,
                        "exit_price_part1": curr_price,
                        "exit_price_part2": curr_price,
                        "bars_in_trade": trade.m1_bars_in_trade,
                        "close_time": trade.close_time,
                        "close_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade.close_time))
                    }
                    await self.event_bus.publish("telemetry", {
                        "type": "CANDLESTICK_EXIT_TRIGGERED",
                        "trade_id": trade.trade_id,
                        "reason": candle_reason,
                        "total_pnl": trade.total_pnl,
                        "exit_price": curr_price
                    })
                    print(f"[CANDLESTICK_EXIT] Triggered in IN_POSITION for {trade.trade_id}: {candle_reason} (PnL: {trade.total_pnl:+.2f}$)")
                    return trade


        # 3. Check Scratch Rule (Premise Threatened)
        # Only evaluate scratch if trade has had minimum safe holding time to breathe
        if trade.m1_bars_in_trade >= min_holding_bars and trade.state == PositionState.IN_POSITION:
            from core.domain.rules.vector_dynamics import MicroPatternDetector
            atr_1m = MicroPatternDetector.calculate_atr(bars_m1, period=14)
            bar_range = curr_bar.high - curr_bar.low

            if getattr(trade, "initial_risk_dist", None) is None or trade.initial_risk_dist <= 0:
                trade.initial_risk_dist = abs(trade.part1.entry_price - trade.part1.sl_price)
            risk_dist = trade.initial_risk_dist
            t1_dist = abs(trade.part1.tp_price - trade.part1.entry_price)

            # Tier 1 Fast Scratch: Opposite momentum bar MUST have significant range (>= 1.5 ATR)
            is_significant_range = (bar_range >= max(1.5 * atr_1m, 0.5)) if atr_1m > 0 else True

            # Tier 2: Check M3 structure breach (confirmed close against trade direction)
            m3_structure_broken = False
            if bars_m3 and len(bars_m3) >= 2:
                latest_m3 = bars_m3[-1]
                if trade.side == OrderSide.BUY:
                    m3_structure_broken = (latest_m3.close < latest_m3.open) and (latest_m3.close < (trade.part1.entry_price - 0.5 * risk_dist))
                else:
                    m3_structure_broken = (latest_m3.close > latest_m3.open) and (latest_m3.close > (trade.part1.entry_price + 0.5 * risk_dist))

            if trade.side == OrderSide.BUY:
                profit_dist = curr_price - trade.part1.entry_price
                opp_momentum = is_significant_range and (curr_bar.close < curr_bar.open) and (curr_bar.close < trade.part1.entry_price) and (risk_dist > 0 and (trade.part1.entry_price - curr_price) >= 0.65 * risk_dist)
            else:
                profit_dist = trade.part1.entry_price - curr_price
                opp_momentum = is_significant_range and (curr_bar.close > curr_bar.open) and (curr_bar.close > trade.part1.entry_price) and (risk_dist > 0 and (curr_price - trade.part1.entry_price) >= 0.65 * risk_dist)

            unrealized_r = profit_dist / risk_dist if risk_dist > 0 else 0.0
            price_progress = profit_dist / t1_dist if t1_dist > 0 else 0.0

            is_scratch, scratch_reason = RiskManager.evaluate_scratch_rule(
                bars_in_trade=trade.m1_bars_in_trade,
                scratch_timeout_bars=scratch_timeout_bars,
                opposite_momentum_detected=opp_momentum,
                unrealized_r=unrealized_r,
                price_progress_pct=price_progress,
                grace_period_bars=4,
                m3_structure_broken=m3_structure_broken
            )
            if is_scratch:
                trade.state = PositionState.SCRATCHED
                trade.close_time = time.time()
                profile = get_instrument_profile(trade.symbol)
                # Close active tickets on broker before marking parts as closed
                closed_tickets = set()
                for part in [trade.part1, trade.part2]:
                    if part.ticket and part.ticket not in closed_tickets and not part.is_closed:
                        await self.broker.close_position(part.ticket)
                        closed_tickets.add(part.ticket)
                    part.is_closed = True
                    part.close_price = curr_price
                    part.pnl = calculate_pnl(part.entry_price, curr_price, part.lot_size, trade.side, profile)

                is_profit_scratch = trade.total_pnl >= 1.0
                scratch_type = "PROFIT_SCRATCH" if is_profit_scratch else "SCRATCH_TRIGGERED"
                trade.close_context = {
                    "close_state": "SCRATCHED",
                    "close_reason": f"{scratch_reason} ({'Profit Secured' if is_profit_scratch else 'Scratch'})",
                    "total_pnl": trade.total_pnl,
                    "exit_price_part1": curr_price,
                    "exit_price_part2": curr_price,
                    "bars_in_trade": trade.m1_bars_in_trade,
                    "close_time": trade.close_time,
                    "close_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade.close_time))
                }

                await self.event_bus.publish("telemetry", {
                    "type": scratch_type,
                    "trade_id": trade.trade_id,
                    "total_pnl": trade.total_pnl,
                    "reason": scratch_reason
                })
                return trade

        # 4. State: TRAILING_STOP (Part 2 Active Management)
        if trade.state == PositionState.TRAILING_STOP:
            # Strictly count trailing bars only on new M1 candle timestamp
            if trade.last_trailing_bar_timestamp is None:
                trade.last_trailing_bar_timestamp = curr_bar.timestamp
                trade.bars_in_trailing = 1
            elif trade.last_trailing_bar_timestamp != curr_bar.timestamp:
                trade.last_trailing_bar_timestamp = curr_bar.timestamp
                trade.bars_in_trailing += 1

            # (A) Check Part 2 Trailing SL Hit
            part2_sl_hit = False
            if trade.side == OrderSide.BUY and curr_bar.low <= trade.part2.sl_price:
                part2_sl_hit = True
            elif trade.side == OrderSide.SELL and curr_bar.high >= trade.part2.sl_price:
                part2_sl_hit = True

            if part2_sl_hit:
                trade.state = PositionState.FULLY_CLOSED
                trade.close_time = time.time()
                profile = get_instrument_profile(trade.symbol)
                trade.part2.is_closed = True
                trade.part2.close_price = trade.part2.sl_price
                trade.part2.pnl = calculate_pnl(trade.part2.entry_price, trade.part2.sl_price, trade.part2.lot_size, trade.side, profile)
                if trade.part2.ticket:
                    await self.broker.close_position(trade.part2.ticket)
                
                close_reason = "PART2_TRAILING_SL_HIT" if trade.profit_protection_level > 0 else "PART2_BREAKEVEN_HIT_AFTER_T1"
                trade.close_context = {
                    "close_state": "FULLY_CLOSED",
                    "close_reason": close_reason,
                    "total_pnl": trade.total_pnl,
                    "profit_protection_level": trade.profit_protection_level,
                    "max_unrealized_r_part2": trade.max_unrealized_r_part2,
                    "exit_price_part1": trade.part1.close_price,
                    "exit_price_part2": trade.part2.close_price,
                    "bars_in_trade": trade.m1_bars_in_trade,
                    "bars_in_trailing": trade.bars_in_trailing,
                    "close_time": trade.close_time,
                    "close_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade.close_time))
                }
                await self.event_bus.publish("telemetry", {
                    "type": "PART2_SL_HIT_FULLY_CLOSED",
                    "trade_id": trade.trade_id,
                    "total_pnl": trade.total_pnl,
                    "reason": close_reason,
                    "locked_level": trade.profit_protection_level
                })
                return trade

            # (B) Check T2 Target Hit
            t2_hit = False
            if trade.side == OrderSide.BUY and curr_bar.high >= trade.part2.tp_price:
                t2_hit = True
            elif trade.side == OrderSide.SELL and curr_bar.low <= trade.part2.tp_price:
                t2_hit = True

            if t2_hit:
                trade.state = PositionState.FULLY_CLOSED
                trade.close_time = time.time()
                profile = get_instrument_profile(trade.symbol)
                trade.part2.is_closed = True
                trade.part2.close_price = trade.part2.tp_price
                trade.part2.pnl = calculate_pnl(trade.part2.entry_price, trade.part2.tp_price, trade.part2.lot_size, trade.side, profile)
                trade.close_context = {
                    "close_state": "FULLY_CLOSED",
                    "close_reason": "T2_TARGET_HIT",
                    "total_pnl": trade.total_pnl,
                    "exit_price_part1": trade.part1.close_price,
                    "exit_price_part2": trade.part2.close_price,
                    "bars_in_trade": trade.m1_bars_in_trade,
                    "bars_in_trailing": trade.bars_in_trailing,
                    "close_time": trade.close_time,
                    "close_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade.close_time))
                }
                if trade.part2.ticket:
                    await self.broker.close_position(trade.part2.ticket)
                await self.event_bus.publish("telemetry", {
                    "type": "T2_HIT_FULLY_CLOSED",
                    "trade_id": trade.trade_id,
                    "total_pnl": trade.total_pnl
                })
                return trade

            # -------------------------------------------------------------
            # CANDLESTICK EXIT ENGINE (Chốt Part 2 chủ động theo mô hình nến)
            # -------------------------------------------------------------
            profit_cfg = getattr(CONFIG, "profit_protection", None)
            enable_candlestick_exits = getattr(profit_cfg, "ENABLE_CANDLESTICK_EXITS", True)
            if enable_candlestick_exits and trade.part2.lot_size > 0:
                profile = get_instrument_profile(trade.symbol)
                min_part2_usd = 2.0 * (trade.part2.lot_size / 0.01)
                curr_part2_pnl = calculate_pnl(trade.part2.entry_price, curr_price, trade.part2.lot_size, trade.side, profile)
                min_candle_r = getattr(profit_cfg, "CANDLESTICK_EXIT_MIN_R", 0.5)
                should_candle_exit = False
                candle_reason = ""
                if curr_part2_pnl >= min_part2_usd:
                    should_candle_exit, candle_reason = CandlestickEngine.evaluate_candlestick_exit(
                        trade=trade,
                        bars_m1=bars_m1,
                        bars_m3=bars_m3,
                        min_r=min_candle_r,
                        curr_price=curr_price
                    )
                if should_candle_exit:
                    trade.state = PositionState.FULLY_CLOSED
                    trade.close_time = time.time()
                    trade.part2.is_closed = True
                    trade.part2.close_price = curr_price
                    trade.part2.pnl = calculate_pnl(trade.part2.entry_price, curr_price, trade.part2.lot_size, trade.side, profile)
                    if trade.part2.ticket:
                        await self.broker.close_position(trade.part2.ticket)

                    trade.close_context = {
                        "close_state": "FULLY_CLOSED",
                        "close_reason": candle_reason,
                        "total_pnl": trade.total_pnl,
                        "profit_protection_level": trade.profit_protection_level,
                        "max_unrealized_r_part2": trade.max_unrealized_r_part2,
                        "exit_price_part1": trade.part1.close_price,
                        "exit_price_part2": curr_price,
                        "bars_in_trade": trade.m1_bars_in_trade,
                        "bars_in_trailing": trade.bars_in_trailing,
                        "close_time": trade.close_time,
                        "close_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade.close_time))
                    }

                    lesson_record = ProfitProtector.build_trading_experience_lesson(
                        trade=trade,
                        trigger_type="CANDLESTICK_EXIT",
                        reason=candle_reason,
                        bars_m1=bars_m1,
                        bars_m3=bars_m3
                    )
                    trade.profit_protection_events.append(lesson_record)
                    try:
                        store = JsonLessonRulesStore()
                        store.append_from_structured([lesson_record["structured_lesson"]])
                    except Exception as ex:
                        print(f"[CANDLESTICK_EXIT] Could not persist lesson rule: {ex}")

                    await self.event_bus.publish("telemetry", {
                        "type": "CANDLESTICK_EXIT_TRIGGERED",
                        "trade_id": trade.trade_id,
                        "reason": candle_reason,
                        "total_pnl": trade.total_pnl,
                        "exit_price": curr_price,
                        "lesson_saved": True
                    })
                    print(f"[CANDLESTICK_EXIT] Part 2 Triggered for {trade.trade_id}: {candle_reason} (PnL: {trade.total_pnl:+.2f}$)")
                    return trade

            # -------------------------------------------------------------
            # LỚP 2: MOMENTUM REVERSAL GUARD (Thoát sớm trước khi bị quét SL)
            # -------------------------------------------------------------
            should_guard_exit, guard_reason = ProfitProtector.evaluate_momentum_guard(trade, bars_m1, curr_price)
            if should_guard_exit:
                trade.state = PositionState.FULLY_CLOSED
                trade.close_time = time.time()
                profile = get_instrument_profile(trade.symbol)
                trade.part2.is_closed = True
                trade.part2.close_price = curr_price
                trade.part2.pnl = calculate_pnl(trade.part2.entry_price, curr_price, trade.part2.lot_size, trade.side, profile)
                if trade.part2.ticket:
                    await self.broker.close_position(trade.part2.ticket)

                trade.close_context = {
                    "close_state": "FULLY_CLOSED",
                    "close_reason": "MOMENTUM_REVERSAL_GUARD",
                    "total_pnl": trade.total_pnl,
                    "guard_detail": guard_reason,
                    "profit_protection_level": trade.profit_protection_level,
                    "max_unrealized_r_part2": trade.max_unrealized_r_part2,
                    "exit_price_part1": trade.part1.close_price,
                    "exit_price_part2": curr_price,
                    "bars_in_trade": trade.m1_bars_in_trade,
                    "bars_in_trailing": trade.bars_in_trailing,
                    "close_time": trade.close_time,
                    "close_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trade.close_time))
                }

                # Ghi chép kinh nghiệm giao dịch & lưu vào JsonLessonRulesStore
                lesson_record = ProfitProtector.build_trading_experience_lesson(
                    trade=trade,
                    trigger_type="MOMENTUM_GUARD",
                    reason=guard_reason,
                    bars_m1=bars_m1,
                    bars_m3=bars_m3
                )
                trade.profit_protection_events.append(lesson_record)
                try:
                    store = JsonLessonRulesStore()
                    store.append_from_structured([lesson_record["structured_lesson"]])
                except Exception as ex:
                    print(f"[PROFIT_PROTECTOR] Could not persist lesson rule: {ex}")

                await self.event_bus.publish("telemetry", {
                    "type": "MOMENTUM_GUARD_EXIT",
                    "trade_id": trade.trade_id,
                    "reason": guard_reason,
                    "exit_price": curr_price,
                    "lesson_saved": True
                })
                print(f"[PROFIT_PROTECTOR] [MOMENTUM_GUARD] Triggered for {trade.trade_id}: {guard_reason}")
                return trade

            # -------------------------------------------------------------
            # STOP LOSS TRAILING / MOVING (Chỉ chạy khi bật cờ cấu hình)
            # -------------------------------------------------------------
            enable_sl_trailing = getattr(profit_cfg, "ENABLE_SL_TRAILING", getattr(getattr(CONFIG, "risk", None), "ENABLE_SL_TRAILING", False))
            enable_ratchet = getattr(profit_cfg, "ENABLE_RATCHET_SL", False) or (enable_sl_trailing and getattr(profit_cfg, "ENABLED", True))
            enable_time_lock = getattr(profit_cfg, "ENABLE_TIME_LOCK", False) or (enable_sl_trailing and getattr(profit_cfg, "ENABLED", True))
            enable_swing_trailing = getattr(profit_cfg, "ENABLE_SWING_TRAILING", False) or (enable_sl_trailing and getattr(profit_cfg, "ENABLED", True))

            # LỚP 1: DYNAMIC R-MULTIPLE RATCHET SL (Nâng SL theo mốc R)
            if enable_ratchet:
                new_ratchet_sl = ProfitProtector.evaluate_ratchet_sl(trade, curr_price, bars_m1=bars_m1)
                if new_ratchet_sl is not None:
                    old_sl = trade.part2.sl_price
                    trade.part2.sl_price = new_ratchet_sl
                    if trade.part2.ticket:
                        await self.broker.modify_position(trade.part2.ticket, sl=trade.part2.sl_price, tp=trade.part2.tp_price)

                    event_msg = f"R-Multiple Ratchet Level {trade.profit_protection_level} locked at {new_ratchet_sl:.2f} (from {old_sl:.2f})"
                    lesson_record = ProfitProtector.build_trading_experience_lesson(
                        trade=trade,
                        trigger_type="RATCHET_LOCK",
                        reason=event_msg,
                        bars_m1=bars_m1,
                        bars_m3=bars_m3
                    )
                    trade.profit_protection_events.append(lesson_record)
                    try:
                        store = JsonLessonRulesStore()
                        store.append_from_structured([lesson_record["structured_lesson"]])
                    except Exception as ex:
                        print(f"[PROFIT_PROTECTOR] Could not persist lesson rule: {ex}")

                    await self.event_bus.publish("telemetry", {
                        "type": "RATCHET_SL_UPDATED",
                        "trade_id": trade.trade_id,
                        "level": trade.profit_protection_level,
                        "new_sl": trade.part2.sl_price,
                        "old_sl": old_sl
                    })
                    print(f"[PROFIT_PROTECTOR] [RATCHET_LOCK] {event_msg}")

            # LỚP 3: ADAPTIVE TIME-BASED PROFIT LOCK (Quá hạn thời gian)
            if enable_time_lock:
                new_time_lock_sl = ProfitProtector.evaluate_time_lock(trade, curr_price, bars_m1=bars_m1)
                if new_time_lock_sl is not None:
                    old_sl = trade.part2.sl_price
                    trade.part2.sl_price = new_time_lock_sl
                    if trade.part2.ticket:
                        await self.broker.modify_position(trade.part2.ticket, sl=trade.part2.sl_price, tp=trade.part2.tp_price)

                    event_msg = f"Time-based profit lock triggered ({trade.bars_in_trailing} bars), new SL {new_time_lock_sl:.2f}"
                    lesson_record = ProfitProtector.build_trading_experience_lesson(
                        trade=trade,
                        trigger_type="TIME_LOCK",
                        reason=event_msg,
                        bars_m1=bars_m1,
                        bars_m3=bars_m3
                    )
                    trade.profit_protection_events.append(lesson_record)
                    try:
                        store = JsonLessonRulesStore()
                        store.append_from_structured([lesson_record["structured_lesson"]])
                    except Exception as ex:
                        print(f"[PROFIT_PROTECTOR] Could not persist lesson rule: {ex}")

                    await self.event_bus.publish("telemetry", {
                        "type": "TIME_LOCK_SL_SET",
                        "trade_id": trade.trade_id,
                        "new_sl": trade.part2.sl_price,
                        "bars_in_trailing": trade.bars_in_trailing
                    })
                    print(f"[PROFIT_PROTECTOR] [TIME_LOCK] {event_msg}")

            # Trailing stop update on newly confirmed TTF (3m) or M1 swing node
            if enable_swing_trailing:
                swings_candidates = []
                swings_3m = SwingDetector.detect_swings(bars_m3)
                if swings_3m:
                    swings_candidates.append(swings_3m[-1])
                if bars_m1 and len(bars_m1) >= 5:
                    swings_1m = SwingDetector.detect_swings(bars_m1)
                    if swings_1m:
                        swings_candidates.append(swings_1m[-1])

                from core.domain.rules.vector_dynamics import MicroPatternDetector
                atr_1m_val = MicroPatternDetector.calculate_atr(bars_m1, period=14) if bars_m1 else 1.0
                profile = get_instrument_profile(trade.symbol)
                d_safe = ProfitProtector.calculate_safe_breathing_distance(bars_m1, profile, min_distance=1.0)
                invalidation_cushion = max(0.3 * atr_1m_val, 0.35)

                for swing in swings_candidates:
                    if trade.side == OrderSide.BUY and swing.swing_type == SwingType.SWING_LOW:
                        target_sl = round(swing.price - invalidation_cushion, profile.digits)
                        if target_sl > trade.part2.sl_price and (curr_price - target_sl) >= d_safe:
                            trade.part2.sl_price = target_sl
                            if trade.part2.ticket:
                                await self.broker.modify_position(trade.part2.ticket, sl=trade.part2.sl_price, tp=trade.part2.tp_price)
                    elif trade.side == OrderSide.SELL and swing.swing_type == SwingType.SWING_HIGH:
                        target_sl = round(swing.price + invalidation_cushion, profile.digits)
                        if target_sl < trade.part2.sl_price and (target_sl - curr_price) >= d_safe:
                            trade.part2.sl_price = target_sl
                            if trade.part2.ticket:
                                await self.broker.modify_position(trade.part2.ticket, sl=trade.part2.sl_price, tp=trade.part2.tp_price)


        return trade

