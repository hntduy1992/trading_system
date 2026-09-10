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

class ModifyPositionRequest(BaseModel):
    ticket_id: int
    sl: float
    tp: float

class ManualOrderRequest(BaseModel):
    symbol: Optional[str] = None
    side: str   # "BUY" or "SELL"
    order_type: str = "MARKET"  # "MARKET", "LIMIT", "STOP"
    volume: float
    price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    comment: str = "MANUAL_ORDER"

class UpdateRiskConfigRequest(BaseModel):
    fixed_lot_size: Optional[float] = None
    account_risk_limit_percent: Optional[float] = None
    manual_sl: Optional[float] = None
    manual_tp1: Optional[float] = None
    manual_tp2: Optional[float] = None

class DeployPlanRequest(BaseModel):
    config: Dict[str, Any]

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
        term_status = await broker.get_terminal_status()
        return {
            "server": "Server A (Execution)",
            "port": 29120,
            "status": "RUNNING",
            "is_paused": circuit_breaker.is_paused,
            "balance": balance,
            "symbol": state_ref.get("symbol", "XAUUSD"),
            "regime": state_ref.get("session_config", {}).get("market_regime", "UNKNOWN"),
            "active_positions_count": len([t for t in trades if t.state == PositionState.IN_POSITION]),
            "pending_orders_count": len([t for t in trades if t.state == PositionState.PENDING_ENTRY]),
            "autotrading": term_status
        }

    @app.get("/api/autotrading_status")
    async def get_autotrading_status():
        return await broker.get_terminal_status()

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

    @app.post("/api/trade/place")
    async def place_manual_order(req: ManualOrderRequest):
        from core.domain.models import OrderSide, SetupType, PositionPart, PositionState, TradeLifecycle, get_instrument_profile
        import time

        sym = req.symbol or state_ref.get("symbol", "XAUUSD")
        side = OrderSide.BUY if req.side.upper() == "BUY" else OrderSide.SELL
        profile = get_instrument_profile(sym)

        # If price not given for MARKET order, get current tick
        order_price = req.price
        if not order_price:
            info = await broker.get_symbol_info(sym)
            order_price = info.get("ask", 0.0) if side == OrderSide.BUY else info.get("bid", 0.0)
            if not order_price or order_price <= 0:
                order_price = profile.base_price

        # If SL or TP not specified, calculate reasonable defaults based on instrument profile
        sl_val = req.sl
        tp_val = req.tp
        if sl_val is None:
            sl_val = order_price - profile.min_buffer_points if side == OrderSide.BUY else order_price + profile.min_buffer_points
        if tp_val is None:
            tp_val = order_price + profile.default_t1_points if side == OrderSide.BUY else order_price - profile.default_t1_points

        ticket = await broker.place_order(
            symbol=sym,
            side=side,
            order_type=req.order_type.upper(),
            volume=float(req.volume),
            price=float(order_price),
            sl=float(sl_val),
            tp=float(tp_val),
            comment=req.comment
        )

        if not ticket:
            raise HTTPException(status_code=400, detail="Lệnh bị Broker từ chối. Vui lòng kiểm tra nút Algo Trading trên MT5 hoặc thông số Volume/SL/TP.")

        trade_id = f"manual_{int(time.time()*1000)}"
        lot_total = float(req.volume)
        lot_p1 = round(lot_total * 0.5, 2)
        lot_p2 = round(lot_total - lot_p1, 2)

        initial_state = PositionState.IN_POSITION if req.order_type.upper() == "MARKET" else PositionState.PENDING_ENTRY

        lifecycle = TradeLifecycle(
            trade_id=trade_id,
            symbol=sym,
            setup_type=SetupType.PB,
            side=side,
            state=initial_state,
            part1=PositionPart(1, lot_p1, float(order_price), float(sl_val), float(tp_val), ticket=ticket),
            part2=PositionPart(2, lot_p2, float(order_price), float(sl_val), float(tp_val), ticket=ticket),
            open_time=time.time(),
            limit_order_ticket=ticket if req.order_type.upper() == "LIMIT" else None,
            stop_order_ticket=ticket if req.order_type.upper() == "STOP" else None
        )

        trades: List[TradeLifecycle] = state_ref.get("active_trades", [])
        trades.append(lifecycle)

        await event_bus.publish("trade_opened", {
            "trade_id": trade_id,
            "setup": "MANUAL",
            "side": side.value,
            "type": req.order_type.upper(),
            "ticket": ticket,
            "lots": {"total": lot_total, "p1": lot_p1, "p2": lot_p2},
            "sl": sl_val,
            "tp": tp_val
        })

        return {
            "status": "SUCCESS",
            "ticket": ticket,
            "trade_id": trade_id,
            "side": side.value,
            "volume": lot_total,
            "sl": sl_val,
            "tp": tp_val
        }

    @app.post("/api/trade/modify")
    async def modify_trade_position(req: ModifyPositionRequest):
        res = await broker.modify_position(req.ticket_id, sl=float(req.sl), tp=float(req.tp))
        if not res:
            raise HTTPException(status_code=400, detail="Không thể cập nhật SL/TP trên Broker MT5.")

        # Update in active_trades state
        trades: List[TradeLifecycle] = state_ref.get("active_trades", [])
        for t in trades:
            if t.part1.ticket == req.ticket_id or t.part2.ticket == req.ticket_id or t.limit_order_ticket == req.ticket_id:
                t.part1.sl_price = float(req.sl)
                t.part1.tp_price = float(req.tp)
                t.part2.sl_price = float(req.sl)
                t.part2.tp_price = float(req.tp)

        return {"status": "SUCCESS", "ticket": req.ticket_id, "sl": req.sl, "tp": req.tp}

    @app.get("/api/risk_config")
    async def get_risk_config():
        cfg: SessionConfig = state_ref.get("session_config_obj")
        rm = cfg.risk_management if cfg else {}
        radar = state_ref.get("setup_radar", {})
        return {
            "fixed_lot_size": rm.get("fixed_lot_size"),
            "account_risk_limit_percent": rm.get("account_risk_limit_percent", 1.0),
            "manual_sl": rm.get("manual_sl"),
            "manual_tp1": rm.get("manual_tp1"),
            "manual_tp2": rm.get("manual_tp2"),
            "suggested": {
                "lot_total": radar.get("lot_total", 0.17 if "XAU" in state_ref.get("symbol", "XAUUSD") else 0.50),
                "sl": radar.get("s1"),
                "tp1": radar.get("t1"),
                "tp2": radar.get("t2"),
                "entry": radar.get("entry")
            }
        }

    @app.post("/api/risk_config")
    async def update_risk_config(req: UpdateRiskConfigRequest):
        cfg: SessionConfig = state_ref.get("session_config_obj")
        if cfg:
            if req.fixed_lot_size is not None:
                if req.fixed_lot_size > 0:
                    cfg.risk_management["fixed_lot_size"] = float(req.fixed_lot_size)
                else:
                    cfg.risk_management.pop("fixed_lot_size", None)
            if req.account_risk_limit_percent is not None and req.account_risk_limit_percent > 0:
                cfg.risk_management["account_risk_limit_percent"] = float(req.account_risk_limit_percent)
            if req.manual_sl is not None:
                if req.manual_sl > 0:
                    cfg.risk_management["manual_sl"] = float(req.manual_sl)
                else:
                    cfg.risk_management.pop("manual_sl", None)
            if req.manual_tp1 is not None:
                if req.manual_tp1 > 0:
                    cfg.risk_management["manual_tp1"] = float(req.manual_tp1)
                else:
                    cfg.risk_management.pop("manual_tp1", None)
            if req.manual_tp2 is not None:
                if req.manual_tp2 > 0:
                    cfg.risk_management["manual_tp2"] = float(req.manual_tp2)
                else:
                    cfg.risk_management.pop("manual_tp2", None)

        return {"status": "SUCCESS", "risk_management": cfg.risk_management if cfg else {}}

    @app.post("/api/deploy_plan")
    async def deploy_plan(req: DeployPlanRequest):
        cfg_data = req.config
        try:
            from core.domain.models import SessionConfig, MarketRegime, HTFZone, Significance
            new_plan = SessionConfig(
                session_id=cfg_data.get("session_id", "sess_custom"),
                symbol=cfg_data.get("symbol", state_ref.get("symbol", "XAUUSD")),
                generated_at=cfg_data.get("generated_at", ""),
                market_regime=MarketRegime(cfg_data["market_regime"]),
                resistance_zones=[
                    HTFZone(id=z["id"], high=float(z["high"]), low=float(z["low"]), significance=Significance(z.get("significance", "MAJOR")), zone_type="RESISTANCE")
                    for z in cfg_data.get("htf_zones", {}).get("resistance_zones", [])
                ],
                support_zones=[
                    HTFZone(id=z["id"], high=float(z["high"]), low=float(z["low"]), significance=Significance(z.get("significance", "MAJOR")), zone_type="SUPPORT")
                    for z in cfg_data.get("htf_zones", {}).get("support_zones", [])
                ],
                setups_enabled=cfg_data.get("setups_enabled", {}),
                execution_rules=cfg_data.get("execution_rules", {}),
                risk_management=cfg_data.get("risk_management", {}),
                news_filter=cfg_data.get("news_filter", {})
            )
            from core.domain.rules.session_manager import SessionManager
            cur_tag = SessionManager().get_session_status().session_tag
            new_plan.session_tag = cfg_data.get("session_tag") or cur_tag

            state_ref["session_config_obj"] = new_plan
            state_ref["session_config"] = {
                "session_id": new_plan.session_id,
                "symbol": new_plan.symbol,
                "market_regime": new_plan.market_regime.value,
                "setups_enabled": new_plan.setups_enabled,
                "session_tag": new_plan.session_tag
            }
            await event_bus.publish("plan_deployed", {
                "session_id": new_plan.session_id,
                "market_regime": new_plan.market_regime.value,
                "setups_enabled": new_plan.setups_enabled,
                "session_tag": new_plan.session_tag
            })
            print(f"[SERVER A] Deployed new AI Session Plan: {new_plan.session_id} (Regime={new_plan.market_regime.value})")
            return {"status": "SUCCESS", "message": f"Plan {new_plan.session_id} deployed to Server A successfully!"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid plan format: {e}")

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
