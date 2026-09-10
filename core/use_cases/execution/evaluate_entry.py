"""
Evaluate Entry Use Case (Server A Execution Engine)
"""
from typing import Dict, Any, List, Optional
import time
from core.domain.models import (
    Bar, SwingNode, SessionConfig, OrderSide, SetupType, 
    WholesaleCalculation, TradeLifecycle, PositionPart, PositionState
)
from core.domain.rules.swing_detector import SwingDetector
from core.domain.rules.vector_dynamics import MicroPatternDetector, VectorDynamicsCalculator
from core.domain.rules.wholesale_engine import WholesaleEngine
from core.domain.rules.risk_manager import RiskManager
from core.domain.rules.setups.setups import TSTSetup, BOFSetup, BPBSetup, PBSetup, CPBSetup
from core.domain.interfaces.broker import IBrokerGateway
from core.domain.interfaces.event_bus import IEventBus

class EvaluateEntryUseCase:
    def __init__(self, broker: IBrokerGateway, event_bus: IEventBus):
        self.broker = broker
        self.event_bus = event_bus
        self.setups = {
            "TST": TSTSetup(),
            "BOF": BOFSetup(),
            "BPB": BPBSetup(),
            "PB": PBSetup(),
            "CPB": CPBSetup()
        }
        self.consumed_anchors: set = set()
        self.last_trade_closed_time: float = 0.0
        self.consecutive_losses: int = 0
        self.total_session_trades: int = 0

    def record_trade_closed(self, trade: TradeLifecycle, is_loss: Optional[bool] = None):
        """Records closed trade outcome to adjust circuit breaker and cooldown timers."""
        self.last_trade_closed_time = trade.close_time or time.time()
        if is_loss is None:
            is_loss = (trade.state == PositionState.STOPPED_OUT)
        
        if is_loss:
            self.consecutive_losses += 1
        elif trade.state == PositionState.FULLY_CLOSED:
            self.consecutive_losses = 0

    def reset_session(self):
        """Resets session tracking statistics."""
        self.consumed_anchors.clear()
        self.last_trade_closed_time = 0.0
        self.consecutive_losses = 0
        self.total_session_trades = 0

    async def execute(
        self,
        config: SessionConfig,
        bars_m30: List[Bar],
        bars_m3: List[Bar],
        bars_m1: List[Bar],
        active_trades: List[TradeLifecycle]
    ) -> Optional[TradeLifecycle]:
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

        # 2. Capital Protection Constraint: Post-Trade Cooldown
        cooldown_secs = exec_rules.get("post_trade_cooldown_seconds", 180)
        now = time.time()
        if self.last_trade_closed_time > 0 and (now - self.last_trade_closed_time) < cooldown_secs:
            return None

        # 3. Capital Protection Constraint: Max Consecutive Losses Circuit Breaker
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

        # 6. Detect 1m Stall Micro-structure (flexible fallback)
        is_stall, stall_low, stall_high = MicroPatternDetector.detect_stall(bars_m1, min_candles=3, atr_factor=1.5)
        if stall_low is None or stall_high is None:
            recent_m1 = bars_m1[-3:] if len(bars_m1) >= 3 else bars_m1
            stall_low = min(b.low for b in recent_m1)
            stall_high = max(b.high for b in recent_m1)

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
                # 8. Single Entry Anchor: 1 Swing structure = 1 Trade only
                latest_swing_time = int(swings_3m[-1].time) if swings_3m else 0
                anchor_id = f"{setup_name}_{side.value}_{round(pullback_price, 2)}_{latest_swing_time}"

                if anchor_id in self.consumed_anchors:
                    # Anchor already traded! Prevents oscillating re-entries around entry price.
                    continue
                # 5. Calculate Wholesale Levels (LWP, LRP, assertion)
                wholesale = WholesaleEngine.calculate_wholesale_levels(
                    setup_type=SetupType(setup_name),
                    side=side,
                    pullback_swing_price=pullback_price,
                    t1_price=t1,
                    t2_price=t2,
                    micro_stall_high=stall_high,
                    micro_stall_low=stall_low,
                    buffer_pts=profile.min_buffer_points
                )

                if not wholesale.is_valid_entry:
                    await self.event_bus.publish("telemetry", {
                        "type": "SETUP_REJECTED",
                        "setup": setup_name,
                        "reason": f"Entry price {wholesale.recommended_entry} fails Wholesale / R:R >= 1.0 boundary"
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

                if lot_total <= 0:
                    continue

                # 7. Smart Order Dispatch (Live MT5 Compatible)
                # Compare recommended entry with current market price:
                # If market price is at or better than wholesale recommended entry -> MARKET entry
                # If market price is approaching -> LIMIT pending entry
                trade_id = f"ytc_{int(time.time()*1000)}"

                if side == OrderSide.BUY:
                    if current_price <= wholesale.recommended_entry + profile.min_buffer_points:
                        order_type = "MARKET"
                        order_price = current_price
                    else:
                        order_type = "LIMIT"
                        order_price = wholesale.recommended_entry
                else:
                    if current_price >= wholesale.recommended_entry - profile.min_buffer_points:
                        order_type = "MARKET"
                        order_price = current_price
                    else:
                        order_type = "LIMIT"
                        order_price = wholesale.recommended_entry

                # Guard: Do not enter if market price already invalidates SL
                if side == OrderSide.BUY and current_price <= actual_sl:
                    continue
                if side == OrderSide.SELL and current_price >= actual_sl:
                    continue

                # Send order to Broker (Live MT5 or Paper)
                order_ticket = await self.broker.place_order(
                    symbol=config.symbol,
                    side=side,
                    order_type=order_type,
                    volume=lot_total,
                    price=order_price,
                    sl=actual_sl,
                    tp=actual_tp1,
                    comment=f"{trade_id}_{setup_name}"
                )

                # CRITICAL: If Broker rejected order, do NOT create phantom trade!
                if not order_ticket:
                    await self.event_bus.publish("telemetry", {
                        "type": "ORDER_FAILED",
                        "setup": setup_name,
                        "reason": "Broker rejected order (check MT5 terminal AlgoTrading status)"
                    })
                    continue

                # Mark anchor as consumed so price wiggles won't re-trigger
                self.consumed_anchors.add(anchor_id)
                self.total_session_trades += 1

                initial_state = PositionState.IN_POSITION if order_type == "MARKET" else PositionState.PENDING_ENTRY

                lifecycle = TradeLifecycle(
                    trade_id=trade_id,
                    symbol=config.symbol,
                    setup_type=SetupType(setup_name),
                    side=side,
                    state=initial_state,
                    part1=PositionPart(1, lot_p1, order_price, actual_sl, actual_tp1, ticket=order_ticket),
                    part2=PositionPart(2, lot_p2, order_price, actual_sl, actual_tp2, ticket=order_ticket),
                    open_time=time.time(),
                    limit_order_ticket=order_ticket if order_type == "LIMIT" else None,
                    stop_order_ticket=None,
                    m1_bars_in_trade=0,
                    last_bar_timestamp=bars_m1[-1].timestamp if bars_m1 else None,
                    anchor_id=anchor_id
                )

                await self.event_bus.publish("trade_opened", {
                    "trade_id": trade_id,
                    "setup": setup_name,
                    "side": side.value,
                    "type": order_type,
                    "ticket": order_ticket,
                    "wholesale": wholesale.__dict__,
                    "lots": {"total": lot_total, "p1": lot_p1, "p2": lot_p2},
                    "anchor_id": anchor_id
                })

                print(f"[ENGINE] Trade Executed: {trade_id} [{setup_name} {side.value} {order_type} ticket={order_ticket} anchor={anchor_id}]")
                return lifecycle

        return None
