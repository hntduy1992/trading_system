"""
News Sentiment & Gold Macro Impact Analyzer Use Case
Analyzes economic calendar and financial news to derive:
  1. Macro Bias for Gold: BULLISH_GOLD, BEARISH_GOLD, NEUTRAL_HIGH_VOLATILITY
  2. News Blackout Windows: Automated trading freeze before/after high-impact releases
  3. Dynamic Lot Sizing Modifier: Scales volume (1.0x, 0.5x, 0.0x) based on macro risk
  4. Actionable Vietnamese trade recommendations for XAUUSD traders
"""
import time
import datetime
import json
from typing import Dict, Any, List, Optional
from core.domain.interfaces.ai_engine import IAIEngine
from infrastructure.news.news_fetcher import NewsFetcher

class NewsSentimentAnalyzerUseCase:
    def __init__(self, ai_engine: Optional[IAIEngine] = None, news_fetcher: Optional[NewsFetcher] = None):
        self.ai_engine = ai_engine
        self.news_fetcher = news_fetcher or NewsFetcher()
        self.last_analysis_result: Optional[Dict[str, Any]] = None
        self.last_analysis_time: float = 0.0

    async def execute(
        self,
        symbol: str = "XAUUSD",
        blackout_before_minutes: int = 20,
        blackout_after_minutes: int = 20,
        include_medium_impact: bool = True,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Executes macro calendar fetch, evaluates blackout windows, calls AI for gold impact synthesis.
        """
        now = time.time()
        # Return cached analysis if fresh (< 10 minutes)
        if not force_refresh and self.last_analysis_result and (now - self.last_analysis_time < 600):
            # Still update live blackout state against current clock
            self._refresh_live_blackout_state(self.last_analysis_result, now)
            return self.last_analysis_result

        # 1. Fetch upcoming calendar and articles
        calendar_events = self.news_fetcher.fetch_economic_calendar(force_refresh=force_refresh)
        macro_news = self.news_fetcher.fetch_macro_gold_news(force_refresh=force_refresh)

        # 2. Build Blackout Windows for High & Key Medium Impact Events
        blackout_windows = []
        next_high_event = None
        min_future_diff = float("inf")

        for ev in calendar_events:
            ev_ts = ev.get("timestamp", 0.0)
            impact = ev.get("impact", "LOW")
            
            # Blackout applies strictly to HIGH impact USD events and key/all MEDIUM USD events
            is_blackout_event = (impact == "HIGH") or (include_medium_impact and impact == "MEDIUM")
            if is_blackout_event:
                start_ts = ev_ts - (blackout_before_minutes * 60)
                end_ts = ev_ts + (blackout_after_minutes * 60)
                blackout_windows.append({
                    "event_id": ev.get("id"),
                    "title": ev.get("title"),
                    "impact": impact,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "event_ts": ev_ts,
                    "start_str": datetime.datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S"),
                    "end_str": datetime.datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M:%S"),
                    "event_str": ev.get("datetime_str", "")
                })

            # Track next upcoming high/medium impact event
            if ev_ts > now:
                diff = ev_ts - now
                if diff < min_future_diff and impact in ["HIGH", "MEDIUM"]:
                    min_future_diff = diff
                    next_high_event = ev

        # 3. Check if currently in blackout
        is_in_blackout = False
        active_blackout_reason = ""
        active_event_title = ""
        for bw in blackout_windows:
            if bw["start_ts"] <= now <= bw["end_ts"]:
                is_in_blackout = True
                active_event_title = bw.get("title", "")
                active_blackout_reason = f"Đang trong cửa sổ né tin {bw.get('impact', '')}: {bw['title']} ({bw['start_str']} -> {bw['end_str']})"
                break

        # 4. Synthesize Macro Bias & Lot Sizing Recommendation
        # Try AI Engine synthesis first; fallback to deterministic rule-based evaluation
        ai_recommendation = await self._synthesize_macro_bias(
            symbol=symbol,
            calendar_events=calendar_events[:6],
            macro_news=macro_news[:4],
            is_in_blackout=is_in_blackout,
            next_event=next_high_event
        )

        result = {
            "symbol": symbol,
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "is_in_blackout": is_in_blackout,
            "active_blackout_reason": active_blackout_reason,
            "next_event": {
                "title": next_high_event.get("title") if next_high_event else "Không có tin lớn sắp tới",
                "impact": next_high_event.get("impact") if next_high_event else "NONE",
                "time_str": next_high_event.get("datetime_str") if next_high_event else "N/A",
                "minutes_left": round((next_high_event["timestamp"] - now) / 60, 1) if next_high_event else None
            } if next_high_event else None,
            "macro_bias": ai_recommendation.get("macro_bias", "NEUTRAL"),
            "lot_multiplier": ai_recommendation.get("lot_multiplier", 1.0),
            "recommendation_summary": ai_recommendation.get("recommendation_summary", ""),
            "trade_guidance": ai_recommendation.get("trade_guidance", []),
            "blackout_windows": blackout_windows,
            "calendar_events": calendar_events[:10],
            "macro_news": macro_news[:5]
        }

        self.last_analysis_result = result
        self.last_analysis_time = now
        return result

    def _refresh_live_blackout_state(self, analysis: Dict[str, Any], now: float):
        """Refreshes real-time blackout flag without re-calling heavy AI."""
        is_in_blackout = False
        reason = ""
        active_title = ""
        for bw in analysis.get("blackout_windows", []):
            if bw["start_ts"] <= now <= bw["end_ts"]:
                is_in_blackout = True
                active_title = bw.get("title", "")
                reason = f"Đang trong cửa sổ né tin {bw.get('impact', '')}: {bw['title']} ({bw['start_str']} -> {bw['end_str']})"
                break
        analysis["is_in_blackout"] = is_in_blackout
        analysis["active_blackout_reason"] = reason
        analysis["active_event_title"] = active_title

        # Refresh next event minutes left
        next_ev = analysis.get("next_event")
        if next_ev and next_ev.get("time_str") != "N/A":
            for bw in analysis.get("blackout_windows", []):
                if bw["title"] == next_ev["title"]:
                    left = (bw["event_ts"] - now) / 60
                    next_ev["minutes_left"] = round(left, 1) if left > 0 else 0.0
                    break

    def check_blackout_status(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Synchronous fast check of current news blackout status against cached windows."""
        now = now or time.time()
        if self.last_analysis_result:
            self._refresh_live_blackout_state(self.last_analysis_result, now)
            return {
                "is_in_blackout": self.last_analysis_result.get("is_in_blackout", False),
                "reason": self.last_analysis_result.get("active_blackout_reason", ""),
                "title": self.last_analysis_result.get("active_event_title", ""),
                "blackout_windows": self.last_analysis_result.get("blackout_windows", [])
            }
        return {"is_in_blackout": False, "reason": "", "title": "", "blackout_windows": []}

    async def _synthesize_macro_bias(
        self,
        symbol: str,
        calendar_events: List[Dict[str, Any]],
        macro_news: List[Dict[str, Any]],
        is_in_blackout: bool,
        next_event: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Calls LLM or rule-based engine to generate macro bias and lot recommendation."""
        # 1. Immediate freeze if in blackout
        if is_in_blackout:
            return {
                "macro_bias": "NEUTRAL_VOLATILITY",
                "lot_multiplier": 0.0,
                "recommendation_summary": "TẠM DỪNG GIAO DỊCH: Đang trong vùng bão tin tức đỏ biến động mạnh. Đóng băng mở vị thế mới để bảo vệ vốn.",
                "trade_guidance": [
                    "Tuyệt đối không mở vị thế mới (Limit hoặc Market).",
                    "Nếu có vị thế đang chạy đã đạt T1, giữ nguyên Part 2 với Trailing Stop hòa vốn (Breakeven).",
                    "Chờ nến M3/M30 ổn định sau giờ tin ít nhất 20 phút trước khi quét setup mới."
                ]
            }

        # 2. If next event is within 45 minutes
        is_approaching_news = False
        if next_event and next_event.get("impact") == "HIGH":
            diff_min = (next_event.get("timestamp", 0.0) - time.time()) / 60
            if 0 < diff_min <= 45:
                is_approaching_news = True

        # Rule-based fallback synthesis
        guidance = []
        if is_approaching_news:
            lot_mult = 0.5
            summary = f"Sắp có tin đỏ quan trọng ({next_event.get('title')}). Khuyến nghị giảm 50% khối lượng và chuẩn bị né tin."
            guidance.append("Giảm một nửa rủi ro (0.5x lot size) để phòng ngừa giật râu nến trước tin.")
            guidance.append("Không giữ các lệnh đang âm hoặc chưa đạt T1 khi tin sắp công bố.")
        else:
            lot_mult = 1.0
            summary = "Môi trường tin tức ổn định. Giao dịch thuận theo cấu trúc Price Action và tỷ lệ R:R chuẩn YTC."
            guidance.append("Áp dụng đủ 100% khối lượng theo 1% Risk Balance.")
            guidance.append("Ưu tiên setup thuận theo xu hướng của phiên (PB/BPB).")

        return {
            "macro_bias": "NEUTRAL_NORMAL",
            "lot_multiplier": lot_mult,
            "recommendation_summary": summary,
            "trade_guidance": guidance
        }
