"""
Post-Trade Evaluator & Session Learning Engine
Evaluates trade performance immediately upon closure, extracts actionable lessons,
ingests lessons into Vector RAG by session, and feeds them back into future trade decisions.
"""
import time
import json
import asyncio
from typing import Dict, Any, List, Optional
from core.domain.models import TradeLifecycle, PositionState, SessionConfig, Bar
from core.domain.interfaces.vector_store import IVectorStore
from core.domain.interfaces.event_bus import IEventBus
from infrastructure.storage.json_store import LocalJsonStore

# ── Compact encoding maps ──────────────────────────────────────────────────────
_SETUP_CODE = {
    "TST": "TST", "BOF": "BOF", "BPB": "BPB", "PB": "PB", "CPB": "CPB",
    "TREND_BAR_FAIL": "TBF", "INSIDE_BAR_SMA21": "IB", "ID_NR4": "IDN4",
    "NR7_EMA20": "NR7", "YUM_YUM": "YY", "MANUAL": "MAN"
}
_REGIME_CODE = {
    "TRENDING_STEADY": "TS", "TRENDING_WEAKENING": "TW",
    "SIDEWAYS_RANGE": "SR", "BREAKOUT_EXPANSION": "BE",
    "CHOPPY_NO_TRADE": "CH", "GENERAL": "GN"
}
_STATE_CODE = {
    "FULLY_CLOSED": "FC", "SCRATCHED": "SC", "STOPPED_OUT": "SO",
    "T1_HIT": "T1", "TRAILING_STOP": "TR", "PENDING_ENTRY": "PE",
    "IN_POSITION": "IP"
}

def _derive_ctx_flags(close_ctx: dict, state_str: str, bars_in_trade: int) -> str:
    """Derive compact context flag string from trade close context."""
    flags = []
    reason = (close_ctx.get("close_reason") or close_ctx.get("reason") or "").lower()
    if any(k in reason for k in ("momentum", "premise", "threatened", "violated")):
        flags.append("mom_vs")
    if any(k in reason for k in ("timeout", "expired", "time-in-force")):
        flags.append("stall_to")
    if any(k in reason for k in ("wick", "reject")):
        flags.append("wick_rej")
    wholesale = close_ctx.get("wholesale") or {}
    if isinstance(wholesale, dict) and wholesale.get("is_valid_entry") is False:
        flags.append("ws_miss")
    if state_str == "STOPPED_OUT":
        flags.append("SL_hit")
    if bars_in_trade <= 2 and state_str == "SCRATCHED":
        flags.append("quick_sc")
    return "|".join(flags) if flags else "std"


class PostTradeEvaluatorUseCase:
    def __init__(
        self,
        ai_engine: Optional[Any],
        vector_store: IVectorStore,
        json_store: LocalJsonStore,
        event_bus: Optional[IEventBus] = None
    ):
        self.ai_engine = ai_engine
        self.vector_store = vector_store
        self.json_store = json_store
        self.event_bus = event_bus

    async def evaluate_and_learn(
        self,
        trade: TradeLifecycle,
        session_cfg: Optional[SessionConfig] = None,
        recent_m1: Optional[List[Bar]] = None,
        recent_m3: Optional[List[Bar]] = None
    ) -> Dict[str, Any]:
        """
        Executes immediate post-trade reflection upon trade finalization.
        Uses deterministic YTC rules only (AI adds no edge vs deterministic here).
        Stores structured numeric records for efficient AI consumption in future calls.
        """
        setup = trade.setup_type.value if hasattr(trade.setup_type, "value") else str(trade.setup_type)
        side = trade.side.value if hasattr(trade.side, "value") else str(trade.side)
        state_str = trade.state.value if hasattr(trade.state, "value") else str(trade.state)
        regime = session_cfg.market_regime.value if session_cfg and hasattr(session_cfg.market_regime, "value") else "GENERAL"
        session_tag = getattr(session_cfg, "session_tag", None) or "CURRENT_SESSION"

        entry_price = trade.part1.entry_price if trade.part1 else 0.0
        sl_price = trade.part1.sl_price if trade.part1 else 0.0
        tp1_price = trade.part1.tp_price if trade.part1 else 0.0
        close_ctx = getattr(trade, "close_context", {}) or {}
        close_reason = close_ctx.get("close_reason") or close_ctx.get("reason", "Standard Lifecycle Exit")
        bars_in_trade = trade.m1_bars_in_trade or close_ctx.get("bars_in_trade", 0)

        pnl = 0.0
        if trade.part1 and hasattr(trade.part1, "pnl"):
            pnl += trade.part1.pnl
        if trade.part2 and hasattr(trade.part2, "pnl"):
            pnl += trade.part2.pnl

        # Compute R:R achieved
        risk_dist = abs(entry_price - sl_price) if sl_price else 0
        if state_str == "FULLY_CLOSED" and tp1_price and risk_dist > 0:
            reward = abs(tp1_price - entry_price)
            rr_achieved = round(reward / risk_dist, 2)
        else:
            rr_achieved = 0.0

        # 1. Deterministic reflection — no AI call needed
        reflection = self._deterministic_reflection(
            setup=setup, side=side, state=state_str,
            entry_price=entry_price, sl_price=sl_price,
            close_reason=close_reason, bars_in_trade=bars_in_trade,
            session_tag=session_tag, pnl=pnl
        )

        # 2. Attach reflection to Trade object
        trade.reflection = reflection
        if hasattr(trade, "close_context") and trade.close_context is not None:
            trade.close_context["reflection"] = reflection

        # 3. Build structured numeric record (AI-native format, replaces Vietnamese text)
        ctx_flags = _derive_ctx_flags(close_ctx, state_str, bars_in_trade)
        record = {
            "s":    _SETUP_CODE.get(setup, setup[:6]),
            "d":    side[0],
            "r":    _STATE_CODE.get(state_str, state_str[:2]),
            "e":    round(entry_price, 2),
            "sl":   round(sl_price, 2),
            "t1":   round(tp1_price, 2),
            "rr":   rr_achieved,
            "bars": bars_in_trade,
            "pnl":  round(pnl, 2),
            "regime": _REGIME_CODE.get(regime, regime[:2]),
            "ctx":  ctx_flags,
            "session_tag": session_tag   # full tag kept for search_lessons filtering
        }
        record_str = json.dumps(record, separators=(",", ":"))

        # 4. Ingest into Vector Store (compact record as lesson text)
        metadata = {
            "trade_id": trade.trade_id,
            "session_tag": session_tag,
            "regime": regime,
            "setup": setup,
            "side": side,
            "outcome": state_str,
            "rating": reflection["rating"],
            "recommendation": reflection["recommendation"],
            "timestamp": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        }
        try:
            await self.vector_store.add_lesson(lesson_text=record_str, metadata=metadata)
        except Exception as e:
            print(f"[POST_TRADE_EVALUATOR] Vector store ingestion warning: {e}")

        # 5. Persist to disk
        try:
            existing = self.json_store.load_session_lessons()
            existing.append({
                **record,
                "trade_id": trade.trade_id,
                "rating": reflection["rating"],
                "timestamp": time.time(),
                "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            })
            self.json_store.save_session_lessons(existing)
        except Exception as e:
            print(f"[POST_TRADE_EVALUATOR] Could not persist session_lessons.json: {e}")

        # 6. Publish telemetry
        if self.event_bus:
            await self.event_bus.publish("telemetry", {
                "type": "POST_TRADE_LESSON_LEARNED",
                "trade_id": trade.trade_id,
                "session_tag": session_tag,
                "setup": setup,
                "side": side,
                "outcome": state_str,
                "rating": reflection["rating"],
                "lesson": reflection["lesson"],
                "recommendation": reflection["recommendation"],
                "record": record_str
            })

        try:
            print(f"[POST_TRADE_EVALUATOR] {trade.trade_id} [{state_str}] Evaluated: {reflection['lesson']}")
        except UnicodeEncodeError:
            safe_lesson = reflection['lesson'].encode('ascii', 'backslashreplace').decode('ascii')
            print(f"[POST_TRADE_EVALUATOR] {trade.trade_id} [{state_str}] Evaluated: {safe_lesson}")
        return reflection

    def _deterministic_reflection(
        self,
        setup: str,
        side: str,
        state: str,
        entry_price: float,
        sl_price: float,
        close_reason: str,
        bars_in_trade: int,
        session_tag: str,
        pnl: float
    ) -> Dict[str, Any]:
        """Deterministic Lance Beggs YTC Price Action rules for trade outcome."""
        if state == "FULLY_CLOSED":
            return {
                "rating": "OPTIMAL_EXECUTION",
                "lesson": f"Setup {setup} {side} tai {entry_price} thanh cong: Vi the tan dung tot vung gia si Wholesale, cau truc thuan da va chot loi ky luat tai cac muc tieu T1/T2.",
                "recommendation": f"Tiep tuc uu tien kich hoat setup {setup} {side} khi thi truong duy tri cau truc tuong tu trong phien {session_tag}."
            }
        elif state == "SCRATCHED":
            if any(k in close_reason for k in ["momentum", "Premise", "threatened", "violated"]):
                return {
                    "rating": "DEFENSIVE_SCRATCH",
                    "lesson": f"Setup {setup} {side} tai {entry_price} thoat Scratch kip thoi: Nhan dien som xung luc doi nghich vi pham tien de, bao toan 100% von truoc khi cham SL.",
                    "recommendation": f"Trong phien {session_tag}, kien nhan doi nen M1 tu choi ro rang va xung luc doi nghich suy yeu han truoc khi mo vi the ke tiep."
                }
            elif any(k in close_reason for k in ["Time-In-Force", "timeout", "expired"]):
                return {
                    "rating": "DEFENSIVE_SCRATCH",
                    "lesson": f"Setup {setup} {side} tai {entry_price} thoat Scratch do can kiet da ({bars_in_trade} nen): Thi truong chung lai khong mo rong huong di, dong lenh tranh rui ro dao chieu bat ngo.",
                    "recommendation": f"Tranh vao lenh {setup} {side} o giua bien do tich luy hep khi thieu khoi luong but pha trong phien {session_tag}."
                }
            else:
                return {
                    "rating": "DEFENSIVE_SCRATCH",
                    "lesson": f"Setup {setup} {side} tai {entry_price} thoat phong thu: Kip thoi dong vi the ({close_reason}) nham bao ve von an toan.",
                    "recommendation": f"Tiep tuc duy tri nguyen tac thoat lenh Scratch khi gia dinh bi de doa trong phien {session_tag}."
                }
        elif state == "STOPPED_OUT":
            return {
                "rating": "STOP_LOSS_HIT",
                "lesson": f"Setup {setup} {side} tai {entry_price} bi dung lo: Don bay gia doi nghich pha vo nguong invalidation S1 ({sl_price}). Vung dem ky thuat hoac vi tri vao lenh can duoc siet chat.",
                "recommendation": f"Nang dieu kien loc cho setup {setup} {side} trong phien {session_tag}; chi vao khi co nen stall xac nhan vung chac va cach xa can Major doi dien."
            }
        else:
            return {
                "rating": "NEUTRAL_EXIT",
                "lesson": f"Lenh {setup} {side} tai {entry_price} ket thuc trang thai {state}: {close_reason}.",
                "recommendation": f"Theo doi chat che phan ung gia quanh nguong {entry_price} trong cac nhip tiep theo cua phien {session_tag}."
            }
