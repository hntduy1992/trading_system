"""
Circuit Breaker & Emergency Intervention Use Case (Section 4.1)
"""
from typing import List
from core.domain.models import TradeLifecycle, PositionState
from core.domain.interfaces.broker import IBrokerGateway
from core.domain.interfaces.event_bus import IEventBus

class CircuitBreakerUseCase:
    def __init__(self, broker: IBrokerGateway, event_bus: IEventBus):
        self.broker = broker
        self.event_bus = event_bus
        self.is_paused = False

    async def panic_close_all(self, active_trades: List[TradeLifecycle]) -> None:
        """Sends immediate market liquidation to MT5 for all open positions."""
        import time
        now = time.time()
        for trade in active_trades:
            if trade.state in [PositionState.IN_POSITION, PositionState.TRAILING_STOP]:
                closed_tickets = set()
                tickets_to_close = []
                if trade.part1.ticket and not trade.part1.is_closed:
                    tickets_to_close.append(trade.part1.ticket)
                if trade.part2.ticket and not trade.part2.is_closed:
                    if trade.part2.ticket not in tickets_to_close:
                        tickets_to_close.append(trade.part2.ticket)

                for tkt in tickets_to_close:
                    ok = await self.broker.close_position(tkt)
                    if ok:
                        closed_tickets.add(tkt)

                if trade.part1.ticket in closed_tickets:
                    trade.part1.is_closed = True
                    if trade.part2.ticket == trade.part1.ticket:
                        trade.part2.is_closed = True
                if trade.part2.ticket in closed_tickets:
                    trade.part2.is_closed = True

                if trade.part1.is_closed and trade.part2.is_closed:
                    trade.state = PositionState.FULLY_CLOSED
                    trade.close_time = now
                    trade.close_context = {
                        "close_state": "FULLY_CLOSED",
                        "close_reason": "MANUAL_PANIC_CLOSE",
                        "bars_in_trade": trade.m1_bars_in_trade,
                        "close_time": now,
                        "close_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
                    }
            elif trade.state == PositionState.PENDING_ENTRY:
                if trade.limit_order_ticket:
                    await self.broker.cancel_order(trade.limit_order_ticket)
                if trade.stop_order_ticket:
                    await self.broker.cancel_order(trade.stop_order_ticket)
                trade.state = PositionState.FULLY_CLOSED
                trade.close_time = now
                trade.close_context = {
                    "close_state": "FULLY_CLOSED",
                    "close_reason": "MANUAL_PANIC_CANCEL_PENDING",
                    "close_time": now,
                    "close_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
                }

        await self.event_bus.publish("emergency", {
            "action": "PANIC_CLOSE_ALL",
            "message": "All orders and positions liquidated immediately."
        })

    def toggle_pause(self) -> bool:
        """Blocks trigger engine, keeps active manager alive."""
        self.is_paused = not self.is_paused
        return self.is_paused

    async def force_scratch(self, ticket_id: int, active_trades: List[TradeLifecycle]) -> bool:
        """Closes a specific ticket immediately."""
        import time
        now = time.time()
        success = await self.broker.close_position(ticket_id)
        if success:
            for trade in active_trades:
                if trade.part1.ticket == ticket_id:
                    trade.part1.is_closed = True
                if trade.part2.ticket == ticket_id:
                    trade.part2.is_closed = True
                if trade.part1.is_closed and trade.part2.is_closed:
                    trade.state = PositionState.SCRATCHED
                    trade.close_time = now
                    trade.close_context = {
                        "close_state": "SCRATCHED",
                        "close_reason": "MANUAL_FORCE_SCRATCH",
                        "bars_in_trade": trade.m1_bars_in_trade,
                        "close_time": now,
                        "close_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
                    }
        else:
            print(f"[CIRCUIT_BREAKER WARNING] force_scratch failed to close ticket {ticket_id} on MT5!")

        await self.event_bus.publish("emergency", {
            "action": "FORCE_SCRATCH",
            "ticket": ticket_id,
            "success": success
        })
        return success
