# YTC Price Action Trading System (v2.1.0-STRICT)

Hệ thống giao dịch thuật toán tự động kết hợp AI dựa trên phương pháp luận **YTC Price Action Trader của Lance Beggs**.

Dự án được xây dựng theo kiến trúc **Clean Architecture (Hexagonal / Ports & Adapters)** có tính tái sử dụng cao, triển khai trên **cùng 1 máy duy nhất**, sử dụng **các dải port an toàn riêng biệt** (không xung đột với các port thông dụng của lập trình viên) và khởi chạy toàn bộ dịch vụ chỉ bằng **1 file duy nhất (`run.py`)**.

---

## 1. Cấu hình Cổng Mạng (Network Ports)

Hệ thống sử dụng các cổng chuyên biệt cao để tránh hoàn toàn xung đột với các cổng phổ biến (3000, 5000, 8000, 8080, 5173...):

| Thành phần | Địa chỉ / URL | Cổng (Port) | Mô tả |
| :--- | :--- | :--- | :--- |
| **Server A & Web UI** | `http://127.0.0.1:29120` | **29120** | Khớp lệnh MT5, Admin Dashboard & Telemetry |
| **WebSocket Stream** | `ws://127.0.0.1:29120/ws/telemetry` | **29120** | Kênh stream nến, trạng thái lệnh thời gian thực |
| **Server B (AI Brain)** | `http://127.0.0.1:29121` | **29121** | AI Pre-session Planning & Post-session Audit |

---

## 2. Kiến trúc Clean Architecture

```
d:\Project\trading_system\
├── run.py                             # [ENTRYPOINT DUY NHẤT] Khởi chạy toàn bộ hệ thống
├── config.py                          # Cấu hình tập trung (Ports, Broker, AI, Risk)
│
├── core/                              # TẦNG DOMAIN & USE CASES (Thuần Python, độc lập Framework)
│   ├── domain/
│   │   ├── models.py                  # Bar, SwingNode, Order, WholesaleCalculation, SessionConfig
│   │   ├── rules/
│   │   │   ├── swing_detector.py      # Quy tắc 5 nến xác định Swing High/Low (M3)
│   │   │   ├── vector_dynamics.py     # Momentum (dP/dT), Projection, Depth & Micro Stall
│   │   │   ├── wholesale_engine.py    # LWP, LRP, bảo toàn R:R >= 1.0, No Chasing
│   │   │   ├── risk_manager.py        # Position Sizing, chia đôi lệnh 50/50, Circuit breaker, Scratch
│   │   │   └── setups/setups.py       # 5 Setup Lance Beggs: TST, BOF, BPB, PB, CPB
│   │   └── interfaces/                # Ports trừu tượng (IBrokerGateway, IAIEngine, IVectorStore, IEventBus)
│   └── use_cases/
│       ├── execution/                 # Server A: evaluate_entry, manage_lifecycle, circuit_breaker
│       └── intelligence/              # Server B: pre_session_planner, hindsight_auditor, self_optimizer
│
├── infrastructure/                    # TẦNG HẠ TẦNG (Triển khai các Ports)
│   ├── brokers/
│   │   ├── paper_broker.py            # Giả lập khớp lệnh ảo (chạy ngay không cần mở MT5)
│   │   └── mt5_broker.py              # Kết nối MT5 Terminal thật qua MetaTrader5 IPC
│   ├── ai/
│   │   ├── mock_ai_adapter.py         # AI giả lập offline chuẩn JSON Schema 2.1.0-STRICT
│   │   └── gemini_adapter.py          # Kết nối Google Gemini API (gemini-2.0-flash)
│   ├── storage/
│   │   ├── memory_vector_store.py     # Vector Store RAG lưu bài học và độ tương đồng Cosine
│   │   └── json_store.py              # Lưu trữ trading_config.json, session_trades.json
│   └── bus/
│       └── async_event_bus.py         # Bus sự kiện bộ nhớ RAM siêu tốc (<1ms)
│
├── presentation/                      # TẦNG GIAO DIỆN & API
│   ├── api/
│   │   ├── server_a_api.py            # FastAPI Server A (Port 29120)
│   │   └── server_b_api.py            # FastAPI Server B (Port 29121)
│   └── web/static/                    # Giao diện Web Dashboard tích hợp (HTML5, JS, CSS)
│       ├── index.html
│       ├── app.js
│       └── styles.css
│
└── tests/
    └── test_system.py                 # Bộ kiểm thử đơn vị cho toàn bộ các quy tắc toán học
```

---

---

## 3. Cấu hình Môi trường & Khởi chạy (Deploy)

Hệ thống hỗ trợ tách biệt file môi trường cho từng chế độ nhằm **bảo mật tuyệt đối, chống rò rỉ API Keys lên Git**:
- `.env.paper`: Cấu hình cho chế độ chạy mô phỏng / backtest (không cần MT5).
- `.env.live`: Cấu hình cho chế độ chạy tài khoản MT5 thật.
- `.env.example`, `.env.paper.example`, `.env.live.example`: Mẫu tham khảo (commit lên Git an toàn).

### Bước 1: Thiết lập cấu hình nhanh (Setup Wizard)
Trước khi chạy lần đầu, bạn có thể chạy kịch bản wizard để nhập API Keys và tự động kiểm tra kết nối:
```powershell
python setup_env.py
```
Hoặc copy từ file mẫu:
```powershell
cp .env.paper.example .env.paper
cp .env.live.example .env.live
```

### Bước 2: Khởi chạy hệ thống

#### Cách 1: Khởi chạy nhanh bằng file `.bat` (Tự động kiểm tra & giải phóng Port):
Bạn chỉ cần nhấp đúp vào file `start.bat` hoặc gõ trên terminal:
```cmd
start.bat
```
Script sẽ tự động:
1. Quét kiểm tra xem cổng 29120 hoặc 29121 có đang bị tiến trình nào chiếm dụng không. Nếu có, tự động đóng tiến trình đó để giải phóng cổng.
2. Hiển thị menu cho bạn chọn chế độ chạy: Paper Trading, Live MT5, Setup Wizard hoặc Test.

Bạn cũng có thể chạy trực tiếp với tham số:
```cmd
start.bat paper     # Tự động giải phóng port và chạy Paper Trading
start.bat live      # Tự động giải phóng port và chạy Live MT5
```

#### Cách 2: Khởi chạy trực tiếp bằng Python:

##### Chế độ Mô phỏng / Paper Trading:
```powershell
python run.py --env paper
```

#### Chế độ Giao dịch Thật với MetaTrader 5:
```powershell
python run.py --env live --symbol XAUUSD
```

#### Truyền trực tiếp API Key qua dòng lệnh (CLI):
```powershell
python run.py --env paper --gemini-api-key "AIzaSy..."
```
*(Bạn cũng có thể nhập hoặc thay đổi API Key bất cứ lúc nào trực tiếp trên Web Dashboard tại Tab Server B).*

---

## 4. Truy cập Giao diện Web Dashboard

Sau khi chạy lệnh trên, mở trình duyệt web:
* **Dashboard Giám sát & Quản trị**: [http://127.0.0.1:29120](http://127.0.0.1:29120)
  * **Tab Server A**: Biểu đồ đa khung thời gian M30, M3, M1 (Lightweight Charts), Bảng vị thế, Dòng log telemetry trực tiếp, Nút khẩn cấp `PANIC CLOSE ALL`, `PAUSE`.
  * **Tab Server B**: Studio AI lập kế hoạch trước phiên (`Generate New AI Plan`), Hậu kiểm sau phiên (`Run Audit`), Điểm tuân thủ và Bài học Vector DB.

---

## 5. Chạy Kiểm thử Đơn vị (Unit Tests)

Kiểm tra tính chính xác của các công thức toán học và máy trạng thái:
```powershell
python -m unittest tests/test_system.py
```
Toàn bộ 5 bài test về Swing 5-bar, LRP/R:R, No Chasing, Risk 50/50, và Scratch Timeout đều được kiểm chứng tự động.
