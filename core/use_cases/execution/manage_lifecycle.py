"""
Manage Position Lifecycle Use Case (Server A Execution Engine)
Implements Section 2.4 Active Lifecycle Management State Machine
"""
from typing import List, Optional
import time
from core.domain.models import (
    Bar, SwingNode, SwingType, TradeLifecycle, PositionState, OrderSide
)
from core.domain.rules.risk_manager import RiskManager
from core.domain.rules.swing_detector import SwingDetector
from core.domain.interfaces.broker import IBrokerGateway
from core.domain.interfaces.event_bus import IEventBus

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
            # Check if price triggered limit entry or stop entry
            filled = False
            if trade.side == OrderSide.BUY:
                if curr_bar.low <= trade.part1.entry_price:
                    filled = True
                    # Cancel alternate stop order
                    if trade.stop_order_ticket:
                        await self.broker.cancel_order(trade.stop_order_ticket)
                elif curr_bar.high >= trade.part2.entry_price:  # LWP Stop fill
                    filled = True
                    if trade.limit_order_ticket:
                        await self.broker.cancel_order(trade.limit_order_ticket)
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
                await self.event_bus.publish("telemetry", {
                    "type": "ORDER_FILLED",
                    "trade_id": trade.trade_id,
                    "state": trade.state.value
                })
            return trade

        # 2. State: IN_POSITION
        # Strictly increment bar counter ONLY when a new M1 bar timestamp occurs
        if trade.last_bar_timestamp is None:
            trade.last_bar_timestamp = curr_bar.timestamp
            trade.m1_bars_in_trade = 1
        elif trade.last_bar_timestamp != curr_bar.timestamp:
            trade.last_bar_timestamp = curr_bar.timestamp
            trade.m1_bars_in_trade += 1

        # Check Stop Loss hit
        sl_hit = False
        if trade.side == OrderSide.BUY and curr_bar.low <= trade.part1.sl_price:
            sl_hit = True
        elif trade.side == OrderSide.SELL and curr_bar.high >= trade.part1.sl_price:
            sl_hit = True

        if sl_hit:
            trade.state = PositionState.STOPPED_OUT
            trade.close_time = time.time()
            if trade.part1.ticket:
                await self.broker.close_position(trade.part1.ticket)
            if trade.part2.ticket:
                await self.broker.close_position(trade.part2.ticket)
            await self.event_bus.publish("telemetry", {
                "type": "STOP_LOSS_HIT",
                "trade_id": trade.trade_id,
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
            # Liquidate Part 1
            trade.part1.is_closed = True
            trade.part1.close_price = trade.part1.tp_price
            if trade.part1.ticket:
                await self.broker.close_position(trade.part1.ticket, trade.part1.lot_size)

            # Modify Part 2 SL to Breakeven (+/- be_buffer depending on instrument)
            from core.domain.models import get_instrument_profile
            profile = get_instrument_profile(trade.symbol)
            be_buffer = profile.be_buffer_points
            new_sl = (trade.part2.entry_price + be_buffer) if trade.side == OrderSide.BUY else (trade.part2.entry_price - be_buffer)
            trade.part2.sl_price = round(new_sl, profile.digits)

            if trade.part2.ticket:
                await self.broker.modify_position(trade.part2.ticket, sl=trade.part2.sl_price, tp=trade.part2.tp_price)

            trade.state = PositionState.TRAILING_STOP
            await self.event_bus.publish("telemetry", {
                "type": "T1_HIT",
                "trade_id": trade.trade_id,
                "new_sl": trade.part2.sl_price
            })
            return trade

        # 3. Check Scratch Rule (Premise Threatened)
        # Only evaluate scratch if trade has had minimum safe holding time to breathe
        if trade.m1_bars_in_trade >= min_holding_bars and trade.state == PositionState.IN_POSITION:
            risk_dist = abs(trade.part1.entry_price - trade.part1.sl_price)
            t1_dist = abs(trade.part1.tp_price - trade.part1.entry_price)
            if trade.side == OrderSide.BUY:
                profit_dist = curr_price - trade.part1.entry_price
                opp_momentum = (curr_bar.close < curr_bar.open) and (curr_bar.close < trade.part1.entry_price) and (risk_dist > 0 and (trade.part1.entry_price - curr_price) >= 0.55 * risk_dist)
            else:
                profit_dist = trade.part1.entry_price - curr_price
                opp_momentum = (curr_bar.close > curr_bar.open) and (curr_bar.close > trade.part1.entry_price) and (risk_dist > 0 and (curr_price - trade.part1.entry_price) >= 0.55 * risk_dist)

            unrealized_r = profit_dist / risk_dist if risk_dist > 0 else 0.0
            price_progress = profit_dist / t1_dist if t1_dist > 0 else 0.0

            is_scratch, scratch_reason = RiskManager.evaluate_scratch_rule(
                bars_in_trade=trade.m1_bars_in_trade,
                scratch_timeout_bars=scratch_timeout_bars,
                opposite_momentum_detected=opp_momentum,
                unrealized_r=unrealized_r,
                price_progress_pct=price_progress
            )
            if is_scratch:
                trade.state = PositionState.SCRATCHED
                trade.close_time = time.time()
                if trade.part1.ticket and not trade.part1.is_closed:
                    await self.broker.close_position(trade.part1.ticket)
                if trade.part2.ticket and not trade.part2.is_closed:
                    await self.broker.close_position(trade.part2.ticket)
                await self.event_bus.publish("telemetry", {
                    "type": "SCRATCH_TRIGGERED",
                    "trade_id": trade.trade_id,
                    "reason": scratch_reason
                })
                return trade

        # 4. State: TRAILING_STOP (Part 2)
        if trade.state == PositionState.TRAILING_STOP:
            # Check T2 Target Hit
            t2_hit = False
            if trade.side == OrderSide.BUY and curr_bar.high >= trade.part2.tp_price:
                t2_hit = True
            elif trade.side == OrderSide.SELL and curr_bar.low <= trade.part2.tp_price:
                t2_hit = True

            if t2_hit:
                trade.state = PositionState.FULLY_CLOSED
                trade.close_time = time.time()
                if trade.part2.ticket:
                    await self.broker.close_position(trade.part2.ticket)
                await self.event_bus.publish("telemetry", {
                    "type": "T2_HIT_FULLY_CLOSED",
                    "trade_id": trade.trade_id
                })
                return trade

            # Trailing stop update on newly confirmed TTF (3m) swing node
            swings_3m = SwingDetector.detect_swings(bars_m3)
            if swings_3m:
                last_swing = swings_3m[-1]
                if trade.side == OrderSide.BUY and last_swing.swing_type == SwingType.SWING_LOW:
                    if last_swing.price > trade.part2.sl_price:
                        trade.part2.sl_price = round(last_swing.price, 5)
                        if trade.part2.ticket:
                            await self.broker.modify_position(trade.part2.ticket, sl=trade.part2.sl_price, tp=trade.part2.tp_price)
                elif trade.side == OrderSide.SELL and last_swing.swing_type == SwingType.SWING_HIGH:
                    if last_swing.price < trade.part2.sl_price:
                        trade.part2.sl_price = round(last_swing.price, 5)
                        if trade.part2.ticket:
                            await self.broker.modify_position(trade.part2.ticket, sl=trade.part2.sl_price, tp=trade.part2.tp_price)

        return trade
