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
        """
        # 1. Do not enter if there is already an active trade for this symbol
        if any(t.state in [PositionState.IN_POSITION, PositionState.PENDING_ENTRY] for t in active_trades):
            return None

        # 2. Detect swings and trend on TTF (M3)
        swings_3m = SwingDetector.detect_swings(bars_m3)
        if not bars_m1:
            return None
        current_price = bars_m1[-1].close
        trend = SwingDetector.evaluate_trend(swings_3m, current_price)

        # 3. Detect 1m Stall Micro-structure
        is_stall, stall_low, stall_high = MicroPatternDetector.detect_stall(bars_m1, min_candles=3)
        if not is_stall or stall_low is None or stall_high is None:
            return None

        from core.domain.models import get_instrument_profile
        profile = get_instrument_profile(config.symbol)

        # 4. Check enabled setups according to config
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
                        "reason": "Entry price fails Wholesale / R:R >= 1.0 boundary"
                    })
                    continue

                # 6. Calculate Position Sizing
                balance = await self.broker.get_account_balance()
                risk_limit = config.risk_management.get("account_risk_limit_percent", 1.0)
                lot_total, lot_p1, lot_p2 = RiskManager.calculate_lot_size(
                    balance=balance,
                    risk_percent=risk_limit,
                    entry_price=wholesale.recommended_entry,
                    sl_price=wholesale.S1,
                    point_size=profile.point,
                    tick_value=profile.tick_value
                )


                # 7. Dispatch Simultaneous OCO: Limit Order at stall + Stop Order at LWP
                trade_id = f"ytc_{int(time.time()*1000)}"
                limit_side_type = "LIMIT"
                stop_side_type = "STOP"

                # Place Order 1: Limit Order at stall boundary
                limit_ticket = await self.broker.place_order(
                    symbol=config.symbol,
                    side=side,
                    order_type=limit_side_type,
                    volume=lot_total,
                    price=wholesale.recommended_entry,
                    sl=wholesale.S1,
                    tp=wholesale.T1,
                    comment=f"{trade_id}_LIMIT"
                )

                # Place Order 2: Stop Order at LWP (Momentum insurance)
                stop_ticket = await self.broker.place_order(
                    symbol=config.symbol,
                    side=side,
                    order_type=stop_side_type,
                    volume=lot_total,
                    price=wholesale.LWP,
                    sl=wholesale.S1,
                    tp=wholesale.T1,
                    comment=f"{trade_id}_STOP"
                )

                lifecycle = TradeLifecycle(
                    trade_id=trade_id,
                    symbol=config.symbol,
                    setup_type=SetupType(setup_name),
                    side=side,
                    state=PositionState.PENDING_ENTRY,
                    part1=PositionPart(1, lot_p1, wholesale.recommended_entry, wholesale.S1, wholesale.T1, ticket=limit_ticket or 0),
                    part2=PositionPart(2, lot_p2, wholesale.recommended_entry, wholesale.S1, wholesale.T2, ticket=stop_ticket or 0),
                    open_time=time.time(),
                    limit_order_ticket=limit_ticket,
                    stop_order_ticket=stop_ticket
                )

                await self.event_bus.publish("trade_opened", {
                    "trade_id": trade_id,
                    "setup": setup_name,
                    "side": side.value,
                    "wholesale": wholesale.__dict__,
                    "lots": {"total": lot_total, "p1": lot_p1, "p2": lot_p2}
                })

                return lifecycle

        return None
