"""
Session Schedule and Transition Manager
Handles Forex / Gold (XAUUSD) session transitions, freeze windows, and strict session plan verification.
"""
from dataclasses import dataclass
from datetime import datetime, timezone, time as dtime
from enum import Enum
from typing import Optional, Tuple, List, Dict, Any
from core.domain.models import SessionConfig

class SessionType(str, Enum):
    ASIA = "ASIA"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"
    ROLLOVER = "ROLLOVER"

@dataclass
class SessionTransitionStatus:
    current_session: SessionType
    session_tag: str                 # Unique per session per day, e.g. "LONDON_20260910"
    in_transition: bool              # True if in volatile handover/freeze window
    transition_name: str             # e.g. "ASIA_TO_LONDON_OPEN"
    seconds_until_stabilized: float  # Countdown to when new session is stable
    is_plan_loaded: bool = False     # Strictly verified whether plan for this session_tag is loaded
    message: str = ""

@dataclass
class TransitionWindow:
    name: str
    from_session: SessionType
    to_session: SessionType
    start_utc: dtime       # Freeze window start
    stabilized_utc: dtime  # Freeze window end & re-plan point

class SessionManager:
    """
    Manages session schedules and transition freeze periods (UTC):
      - Asia -> London Open: 06:45 - 07:30 UTC (13:45 - 14:30 VN) -> London stable at 07:30 UTC.
      - London -> New York Open: 12:45 - 13:30 UTC (19:45 - 20:30 VN) -> NY stable at 13:30 UTC.
      - Day Rollover: 21:45 - 22:30 UTC (04:45 - 05:30 VN) -> Asia early stable at 22:30 UTC.
    """
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.last_replan_session_tag: Optional[str] = None
        self.is_replanning: bool = False

        # Transition windows in UTC
        self.transitions: List[TransitionWindow] = [
            TransitionWindow(
                name="ASIA_TO_LONDON",
                from_session=SessionType.ASIA,
                to_session=SessionType.LONDON,
                start_utc=dtime(6, 45),
                stabilized_utc=dtime(7, 30)
            ),
            TransitionWindow(
                name="LONDON_TO_NEW_YORK",
                from_session=SessionType.LONDON,
                to_session=SessionType.NEW_YORK,
                start_utc=dtime(12, 45),
                stabilized_utc=dtime(13, 30)
            ),
            TransitionWindow(
                name="DAY_ROLLOVER",
                from_session=SessionType.NEW_YORK,
                to_session=SessionType.ASIA,
                start_utc=dtime(21, 45),
                stabilized_utc=dtime(22, 30)
            )
        ]

    def get_session_status(
        self,
        current_cfg: Optional[SessionConfig] = None,
        now_utc: Optional[datetime] = None
    ) -> SessionTransitionStatus:
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        cur_time = now_utc.time()
        date_str = now_utc.strftime("%Y%m%d")

        # 1. Check if currently inside any transition freeze window
        in_trans = False
        trans_name = ""
        secs_left = 0.0
        active_session = self._determine_base_session(cur_time)

        if self.enabled:
            for tw in self.transitions:
                if tw.start_utc <= cur_time < tw.stabilized_utc:
                    in_trans = True
                    trans_name = tw.name
                    active_session = tw.to_session
                    end_dt = datetime.combine(now_utc.date(), tw.stabilized_utc, tzinfo=timezone.utc)
                    secs_left = max(0.0, (end_dt - now_utc).total_seconds())
                    break

        session_tag = f"{active_session.value}_{date_str}"

        # 2. Strict Verification: Check if loaded plan matches current session tag
        plan_loaded = self.is_plan_valid_for_session(current_cfg, session_tag)

        # 3. Formulate status message
        if in_trans:
            mins = int(secs_left // 60)
            secs = int(secs_left % 60)
            msg = f"GIAO PHIÊN ({trans_name}): Tạm dừng lệnh. Ổn định sau {mins:02d}m{secs:02d}s"
        elif not plan_loaded:
            msg = f"CHỜ NẠP PLAN: Phiên {active_session.value} chưa có kế hoạch hợp lệ!"
        else:
            msg = f"PHIÊN HOẠT ĐỘNG: {active_session.value} (Plan {session_tag} hợp lệ)"

        return SessionTransitionStatus(
            current_session=active_session,
            session_tag=session_tag,
            in_transition=in_trans,
            transition_name=trans_name,
            seconds_until_stabilized=secs_left,
            is_plan_loaded=plan_loaded,
            message=msg
        )

    def _determine_base_session(self, cur_time: dtime) -> SessionType:
        """Determines default active session based on UTC time."""
        if dtime(7, 30) <= cur_time < dtime(13, 30):
            return SessionType.LONDON
        elif dtime(13, 30) <= cur_time < dtime(22, 30):
            return SessionType.NEW_YORK
        else:
            return SessionType.ASIA

    def is_plan_valid_for_session(self, current_cfg: Optional[SessionConfig], expected_session_tag: str) -> bool:
        """
        Strict Session Verification:
        Returns True if and only if current_cfg exists and was generated specifically for expected_session_tag.
        """
        if not current_cfg:
            return False

        cfg_tag = getattr(current_cfg, "session_tag", None)
        if cfg_tag and cfg_tag == expected_session_tag:
            return True

        if current_cfg.session_id and expected_session_tag in current_cfg.session_id:
            return True

        return False

    def should_trigger_auto_replan(
        self,
        status: SessionTransitionStatus,
        current_cfg: Optional[SessionConfig]
    ) -> bool:
        """
        Returns True when new session has stabilized and needs a fresh plan generated.
        """
        if not self.enabled:
            return False

        if status.in_transition:
            return False

        if self.is_replanning:
            return False

        if not status.is_plan_loaded and self.last_replan_session_tag != status.session_tag:
            return True

        return False

    def mark_replan_started(self):
        self.is_replanning = True

    def mark_replan_completed(self, session_tag: str):
        self.is_replanning = False
        self.last_replan_session_tag = session_tag
