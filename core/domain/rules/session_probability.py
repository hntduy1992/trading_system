"""
Module 1: Session-Aware Probability Layer (XAUUSD)
====================================================
Mục tiêu: Lọc và điều chỉnh xác suất giao dịch theo phiên giờ Việt Nam (UTC+7).

Dữ liệu thực tế từ 130 lệnh:
  - NR7_EMA20: 37 lệnh, 0 win  → loại hoàn toàn
  - PB: 38 lệnh, WR 5%         → chỉ cho phép ở phiên tốt
  - TST: 16 lệnh, WR 19%       → setup tốt nhất
  - YUM_YUM: 27 lệnh, WR 7%    → chỉ phù hợp phiên có momentum

Spread thực tế ~6.5 USD/lot → DEAD_ZONE (3h–7h VN) bị chặn hoàn toàn.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

# ---------------------------------------------------------------------------
# Cấu hình các phiên giao dịch (giờ VN = UTC+7)
# multiplier = 0.0 → DEAD ZONE, chặn hoàn toàn mọi lệnh
# ---------------------------------------------------------------------------

SESSION_WINDOWS: Dict[str, Dict[str, Any]] = {
    # Phiên chết — spread cao, thanh khoản cạn kiệt sau NY đóng
    # Không được vào lệnh BẤT KỲ setup nào
    "DEAD_ZONE": {
        "start": 3,
        "end": 7,
        "multiplier": 0.0,
        "allowed_setups": [],
        "description": "Phiên chết (3h–7h VN): thanh khoản cạn, spread cao, cấm giao dịch.",
    },

    # Phiên Tokyo — xu hướng yếu, range hẹp
    # Ưu tiên setup fade-the-range và test vùng SR
    "TOKYO": {
        "start": 7,
        "end": 14,
        "multiplier": 0.85,
        "allowed_setups": ["TST", "ID_NR4", "INSIDE_BAR_SMA21", "BOF"],
        "description": "Phiên Tokyo (7h–14h VN): thanh khoản trung bình, ưu tiên fade setup.",
    },

    # London Open — phiên quan trọng nhất buổi sáng, momentum lớn
    # Setup trend-following và breakout hoạt động tốt
    "LONDON_OPEN": {
        "start": 14,
        "end": 17,
        "multiplier": 1.15,
        "allowed_setups": ["PB", "BPB", "CPB", "BOF", "YUM_YUM"],
        "description": "London Open (14h–17h VN): momentum cao nhất, trend-following ưu tiên.",
    },

    # London mid-session — thị trường dần ổn định sau open
    "LONDON": {
        "start": 17,
        "end": 20,
        "multiplier": 1.0,
        "allowed_setups": ["PB", "CPB", "TST", "BOF"],
        "description": "London (17h–20h VN): thị trường ổn định, setup trend tiếp tục.",
    },

    # NY Overlap — phiên thanh khoản cao nhất ngày (London + NY cùng mở)
    # Tất cả setup mạnh nhất hoạt động tốt
    "NY_OVERLAP": {
        "start": 20,
        "end": 24,  # 24 ≡ 0h hôm sau, xử lý đặc biệt trong code
        "multiplier": 1.20,
        "allowed_setups": ["CPB", "YUM_YUM", "BOF", "PB", "BPB"],
        "description": "NY Overlap (20h–24h VN): thanh khoản đỉnh, setup momentum mạnh nhất.",
    },

    # NY Late — cuối phiên NY, momentum giảm dần
    "NY_LATE": {
        "start": 0,
        "end": 3,
        "multiplier": 0.90,
        "allowed_setups": ["PB", "TST"],
        "description": "NY Late (0h–3h VN): cuối phiên NY, chỉ setup đơn giản.",
    },
}


# ---------------------------------------------------------------------------
# Dataclass kết quả session check
# ---------------------------------------------------------------------------

@dataclass
class SessionCheckResult:
    """Kết quả kiểm tra phiên giao dịch."""
    session_name: str           # Tên phiên (VD: "LONDON_OPEN")
    multiplier: float           # Hệ số nhân xác suất (0.0–1.20)
    is_dead_zone: bool          # True nếu phiên DEAD_ZONE
    setup_allowed: bool         # True nếu setup được phép trong phiên
    should_block: bool          # True nếu nên từ chối lệnh
    block_reason: str           # Lý do từ chối (nếu có)
    allowed_setups: List[str]   # Danh sách setup được phép
    description: str            # Mô tả phiên


# ---------------------------------------------------------------------------
# Session Probability Layer
# ---------------------------------------------------------------------------

class SessionProbabilityLayer:
    """
    Layer lọc xác suất theo phiên giao dịch.

    Quy tắc chính:
    1. DEAD_ZONE (3h–7h VN) → chặn 100% mọi lệnh
    2. Setup không nằm trong allowed_setups của phiên → cảnh báo (không hard-fail)
    3. Score cuối = base_score × session_multiplier

    Sử dụng:
        layer = SessionProbabilityLayer()
        session = layer.get_current_session(hour_utc7=15)  # → "LONDON_OPEN"
        blocked, reason = layer.should_block_trade("NR7_EMA20", hour_utc7=15)
    """

    def __init__(self, windows: Optional[Dict[str, Dict[str, Any]]] = None):
        """
        Args:
            windows: Override SESSION_WINDOWS nếu muốn tuỳ chỉnh
                     (hữu ích cho backtesting với múi giờ khác)
        """
        self._windows = windows or SESSION_WINDOWS

    # ------------------------------------------------------------------
    # Method 1: Lấy tên phiên theo giờ VN
    # ------------------------------------------------------------------

    def get_current_session(self, hour_utc7: int) -> str:
        """
        Trả về tên phiên giao dịch dựa trên giờ Việt Nam (UTC+7).

        Args:
            hour_utc7: Giờ theo UTC+7, phạm vi 0–23

        Returns:
            Tên phiên (VD: "LONDON_OPEN", "DEAD_ZONE", ...)
            Trả về "UNKNOWN" nếu không khớp phiên nào.
        """
        h = hour_utc7 % 24  # Chuẩn hoá về 0–23

        for session_name, cfg in self._windows.items():
            start = cfg["start"]
            end = cfg["end"]

            if start < end:
                # Phiên bình thường (VD: 14–17, 7–14)
                if start <= h < end:
                    return session_name
            else:
                # Phiên qua nửa đêm — hiện tại không có trong config
                # Nhưng vẫn xử lý để tương lai mở rộng được
                if h >= start or h < end:
                    return session_name

        return "UNKNOWN"

    # ------------------------------------------------------------------
    # Method 2: Lấy session multiplier
    # ------------------------------------------------------------------

    def get_session_multiplier(self, hour_utc7: int) -> float:
        """
        Trả về hệ số nhân xác suất (multiplier) của phiên hiện tại.

        Ý nghĩa multiplier:
          0.0  → DEAD ZONE, không giao dịch
          0.85 → Tokyo, xác suất thấp hơn baseline
          1.0  → London mid, baseline
          1.15 → London Open, xác suất tốt hơn
          1.20 → NY Overlap, xác suất cao nhất

        Args:
            hour_utc7: Giờ UTC+7

        Returns:
            float multiplier (0.0–1.20)
        """
        session_name = self.get_current_session(hour_utc7)
        cfg = self._windows.get(session_name, {})
        return float(cfg.get("multiplier", 1.0))

    # ------------------------------------------------------------------
    # Method 3: Kiểm tra setup có được phép trong phiên không
    # ------------------------------------------------------------------

    def is_setup_allowed_in_session(
        self, setup: str, hour_utc7: int
    ) -> tuple[bool, str]:
        """
        Kiểm tra setup có nằm trong danh sách allowed_setups của phiên không.

        Args:
            setup:      Tên setup (VD: "PB", "TST", "NR7_EMA20")
            hour_utc7:  Giờ UTC+7

        Returns:
            (allowed: bool, reason: str)
            - (True, "") nếu được phép
            - (False, "lý do") nếu không được phép
        """
        session_name = self.get_current_session(hour_utc7)
        cfg = self._windows.get(session_name, {})
        allowed_setups: List[str] = cfg.get("allowed_setups", [])

        # DEAD_ZONE → không có setup nào được phép
        if session_name == "DEAD_ZONE":
            return False, (
                f"DEAD_ZONE ({cfg.get('start', 3)}h–{cfg.get('end', 7)}h VN): "
                f"cấm giao dịch hoàn toàn vì thanh khoản cạn kiệt."
            )

        # Phiên không có allowed_setups → chặn hết (defensive)
        if not allowed_setups:
            return False, (
                f"Phiên {session_name} không có setup nào được kích hoạt."
            )

        setup_upper = setup.upper()
        if setup_upper not in [s.upper() for s in allowed_setups]:
            return False, (
                f"Setup {setup} KHÔNG phù hợp phiên {session_name} "
                f"(allowed: {', '.join(allowed_setups)}). "
                f"Dữ liệu lịch sử: setup này có WR thấp trong phiên này."
            )

        return True, ""

    # ------------------------------------------------------------------
    # Method 4: Tính score đã điều chỉnh theo phiên
    # ------------------------------------------------------------------

    def get_adjusted_score(
        self, base_score: float, setup: str, hour_utc7: int
    ) -> float:
        """
        Nhân base_score × session_multiplier.
        Nếu DEAD_ZONE → trả về 0.0.

        Args:
            base_score:  Score gốc (0.0–1.0)
            setup:       Tên setup (dùng để kiểm tra allowed)
            hour_utc7:   Giờ UTC+7

        Returns:
            float score đã được điều chỉnh, capped tại 1.0
        """
        multiplier = self.get_session_multiplier(hour_utc7)

        # DEAD_ZONE → luôn trả 0.0
        if multiplier == 0.0:
            return 0.0

        adjusted = base_score * multiplier
        return min(1.0, max(0.0, adjusted))

    # ------------------------------------------------------------------
    # Method 5: Quyết định có nên chặn lệnh không
    # ------------------------------------------------------------------

    def should_block_trade(
        self, setup: str, hour_utc7: int
    ) -> tuple[bool, str]:
        """
        Quyết định có nên từ chối lệnh không dựa trên phiên và setup.

        Logic:
        1. DEAD_ZONE → CHẶN ngay (hard block)
        2. hour_utc7 == -1 → bỏ qua kiểm tra phiên (mode manual/backtest)
        3. Setup không có trong allowed_setups → CẢNH BÁO nhưng KHÔNG chặn
           (vì có thể trader biết rõ hơn; chặn hoàn toàn sẽ quá cứng nhắc)
           *Ngoại lệ*: NR7_EMA20 → luôn chặn do WR lịch sử = 0%

        Args:
            setup:      Tên setup
            hour_utc7:  Giờ UTC+7, truyền -1 để skip kiểm tra phiên

        Returns:
            (should_block: bool, reason: str)
        """
        # Bỏ qua kiểm tra nếu không có thông tin giờ (backtest mode)
        if hour_utc7 < 0:
            return False, ""

        session_name = self.get_current_session(hour_utc7)
        multiplier = self.get_session_multiplier(hour_utc7)

        # --- Luật đặc biệt: NR7_EMA20 bị cấm vĩnh viễn ---
        # Dữ liệu thực: 37 lệnh NR7_EMA20, 0 win (WR = 0%) → LOẠI
        if setup.upper() == "NR7_EMA20":
            return True, (
                "NR7_EMA20 bị cấm vĩnh viễn: 37 lệnh lịch sử, 0 win (WR=0%). "
                "Spread 6.5 USD/lot ăn mòn hoàn toàn edge của setup này."
            )

        # --- DEAD_ZONE: chặn 100% ---
        if multiplier == 0.0:
            cfg = self._windows.get(session_name, {})
            return True, (
                f"DEAD_ZONE {cfg.get('start', 3)}h–{cfg.get('end', 7)}h VN: "
                f"thanh khoản cạn kiệt, spread đặc biệt cao. "
                f"Expected value âm với spread 6.5 USD/lot."
            )

        # --- Setup không phù hợp phiên: CẢNH BÁO, không chặn cứng ---
        allowed, reason = self.is_setup_allowed_in_session(setup, hour_utc7)
        if not allowed:
            # Chỉ chặn nếu WR lịch sử rất thấp (PB < 10% outside good sessions)
            # Hiện tại chỉ cảnh báo, để caller quyết định
            return False, f"[CẢNH BÁO] {reason}"

        return False, ""

    # ------------------------------------------------------------------
    # Method tiện ích: Lấy đầy đủ thông tin phiên
    # ------------------------------------------------------------------

    def get_session_info(self, setup: str, hour_utc7: int) -> SessionCheckResult:
        """
        Trả về đầy đủ thông tin kiểm tra phiên cho một setup.

        Hữu ích để log chi tiết ra dashboard hoặc journal.
        """
        session_name = self.get_current_session(hour_utc7)
        cfg = self._windows.get(session_name, {})
        multiplier = float(cfg.get("multiplier", 1.0))
        allowed_setups = cfg.get("allowed_setups", [])
        is_dead_zone = session_name == "DEAD_ZONE" or multiplier == 0.0

        allowed, _ = self.is_setup_allowed_in_session(setup, hour_utc7)
        should_block, block_reason = self.should_block_trade(setup, hour_utc7)

        return SessionCheckResult(
            session_name=session_name,
            multiplier=multiplier,
            is_dead_zone=is_dead_zone,
            setup_allowed=allowed,
            should_block=should_block,
            block_reason=block_reason,
            allowed_setups=allowed_setups,
            description=cfg.get("description", ""),
        )

    # ------------------------------------------------------------------
    # Tiện ích: In bảng tóm tắt phiên (debug)
    # ------------------------------------------------------------------

    def print_session_schedule(self) -> None:
        """In bảng tóm tắt các phiên ra console (dùng để debug/review)."""
        print(f"{'Phiên':<15} {'Giờ VN':<12} {'Multiplier':<12} {'Allowed Setups'}")
        print("-" * 70)
        for name, cfg in self._windows.items():
            setups_str = ", ".join(cfg.get("allowed_setups", [])) or "NONE"
            print(
                f"{name:<15} "
                f"{cfg['start']:02d}h–{cfg['end']:02d}h     "
                f"{cfg['multiplier']:<12.2f} "
                f"{setups_str}"
            )
