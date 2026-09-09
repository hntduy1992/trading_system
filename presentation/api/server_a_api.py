"""
Server A FastAPI Application (Real-Time Execution & Admin Dashboard)
Port: 29120 (Custom port avoiding common developer ports)
"""
import asyncio
import json
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os

from core.domain.models import SessionConfig, PositionState, TradeLifecycle
from core.domain.interfaces.broker import IBrokerGateway
from core.domain.interfaces.event_bus import IEventBus
from core.use_cases.execution.circuit_breaker import CircuitBreakerUseCase

class ForceScratchRequest(BaseModel):
    ticket_id: int

def create_server_a_app(
    broker: IBrokerGateway,
    event_bus: IEventBus,
    circuit_breaker: CircuitBreakerUseCase,
    state_ref: Dict[str, Any]
) -> FastAPI:
    app = FastAPI(title="Server A - Real-Time Execution Engine", version="2.1.0-STRICT")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Connected WebSocket clients
    active_websockets: List[WebSocket] = []

    # Subscribe event bus to broadcast telemetry & market ticks to WebSockets
    async def broadcast_event(topic: str, data: Any):
        if not active_websockets:
            return
        msg = json.dumps({"topic": topic, "payload": data})
        for ws in list(active_websockets):
            try:
                await ws.send_text(msg)
            except Exception:
                if ws in active_websockets:
                    active_websockets.remove(ws)

    event_bus.subscribe("telemetry", lambda d: broadcast_event("telemetry", d))
    event_bus.subscribe("trade_opened", lambda d: broadcast_event("trade_opened", d))
    event_bus.subscribe("emergency", lambda d: broadcast_event("emergency", d))
    event_bus.subscribe("market_tick", lambda d: broadcast_event("market_tick", d))
    event_bus.subscribe("setup_radar", lambda d: broadcast_event("setup_radar", d))

    @app.get("/api/radar")
    async def get_radar():
        return state_ref.get("setup_radar", {})

    @app.get("/api/status")
    async def get_status():

        balance = await broker.get_account_balance()
        trades = state_ref.get("active_trades", [])
        return {
            "server": "Server A (Execution)",
            "port": 29120,
            "status": "RUNNING",
            "is_paused": circuit_breaker.is_paused,
            "balance": balance,
            "symbol": state_ref.get("symbol", "XAUUSD"),
            "regime": state_ref.get("session_config", {}).get("market_regime", "UNKNOWN"),
            "active_positions_count": len([t for t in trades if t.state == PositionState.IN_POSITION]),
            "pending_orders_count": len([t for t in trades if t.state == PositionState.PENDING_ENTRY])
        }

    @app.get("/api/bars")
    async def get_bars(timeframe: str = "M1", count: int = 100):
        symbol = state_ref.get("symbol", "XAUUSD")
        bars = await broker.get_latest_bars(symbol, timeframe, count)
        return [
            {
                "time": int(b.timestamp),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume
            }
            for b in bars
        ]

    @app.post("/api/simulate_tick")
    async def simulate_tick():
        if hasattr(broker, "advance_tick"):
            b = broker.advance_tick()
            return {"status": "SUCCESS", "price": b.close}
        return {"status": "SKIPPED", "message": "Only available in paper mode"}

    @app.get("/api/trades")
    async def get_trades():
        trades: List[TradeLifecycle] = state_ref.get("active_trades", [])
        return [
            {
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "setup": t.setup_type.value,
                "side": t.side.value,
                "state": t.state.value,
                "part1": t.part1.__dict__,
                "part2": t.part2.__dict__,
                "bars_in_trade": t.m1_bars_in_trade
            }
            for t in trades
        ]

    @app.post("/api/emergency/panic_close")
    async def panic_close():
        trades: List[TradeLifecycle] = state_ref.get("active_trades", [])
        await circuit_breaker.panic_close_all(trades)
        return {"status": "SUCCESS", "message": "All positions liquidated immediately."}

    @app.post("/api/emergency/pause")
    async def toggle_pause():
        paused = circuit_breaker.toggle_pause()
        return {"status": "SUCCESS", "is_paused": paused}

    @app.post("/api/emergency/force_scratch")
    async def force_scratch(req: ForceScratchRequest):
        trades: List[TradeLifecycle] = state_ref.get("active_trades", [])
        res = await circuit_breaker.force_scratch(req.ticket_id, trades)
        if not res:
            raise HTTPException(status_code=400, detail="Ticket not found or could not close.")
        return {"status": "SUCCESS", "ticket": req.ticket_id}

    @app.websocket("/ws/telemetry")
    async def websocket_telemetry(websocket: WebSocket):
        await websocket.accept()
        active_websockets.append(websocket)
        try:
            symbol = state_ref.get("symbol", "XAUUSD")
            m30_bars = await broker.get_latest_bars(symbol, "M30", 50)
            m3_bars = await broker.get_latest_bars(symbol, "M3", 60)
            m1_bars = await broker.get_latest_bars(symbol, "M1", 60)

            def format_bars(blist):
                return [{"time": int(b.timestamp), "open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in blist]

            # Send initial state snapshot with historical bars
            await websocket.send_text(json.dumps({
                "topic": "snapshot",
                "config": state_ref.get("session_config"),
                "symbol": symbol,
                "status": "CONNECTED",
                "setup_radar": state_ref.get("setup_radar"),
                "bars": {
                    "M30": format_bars(m30_bars),
                    "M3": format_bars(m3_bars),
                    "M1": format_bars(m1_bars)
                }
            }))

            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            if websocket in active_websockets:
                active_websockets.remove(websocket)

    # Mount static web UI if available
    web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "static")
    if os.path.exists(web_dir):
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="static")

    return app
