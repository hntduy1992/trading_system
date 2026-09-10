"""
Interactive Deployment Setup Wizard for YTC Price Action Trading System
Configures .env.paper or .env.live securely without exposing keys to Git
"""
import os
import sys
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def test_gemini_key(api_key: str, model_name: str = "gemini-2.0-flash") -> bool:
    print(f"[*] Đang kiểm tra kết nối Gemini API với model '{model_name}'...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": "Test connection"}]}]}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            print(" -> [THÀNH CÔNG] API Key hợp lệ và hoạt động tốt!")
            return True
        else:
            print(f" -> [THẤT BẠI] Lỗi kết nối Gemini API (Mã HTTP: {resp.status_code}):")
            try:
                err_msg = resp.json().get('error', {}).get('message', resp.text)
                print(f"    Chi tiết: {err_msg}")
            except Exception:
                print(f"    Chi tiết: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f" -> [THẤT BẠI] Không thể kết nối đến Google API: {e}")
        return False

def test_openai_key(api_key: str, model_name: str = "gpt-4o") -> bool:
    print(f"[*] Đang kiểm tra kết nối OpenAI API với model '{model_name}'...")
    url = "https://api.openai.com/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            print(" -> [THÀNH CÔNG] OpenAI API Key hợp lệ!")
            return True
        else:
            print(f" -> [THẤT BẠI] OpenAI API trả về mã lỗi: {resp.status_code}")
            return False
    except Exception as e:
        print(f" -> [THẤT BẠI] Không thể kết nối đến OpenAI API: {e}")
        return False

def load_existing_env(filepath: str) -> dict:
    data = {}
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"').strip("'")
    return data

def save_env_file(filepath: str, data: dict):
    lines = []
    lines.append("# =============================================================================\n")
    lines.append(f"# YTC Price Action Trader - Auto-generated Config: {os.path.basename(filepath)}\n")
    lines.append("# DO NOT COMMIT THIS FILE TO GIT REPOSITORY\n")
    lines.append("# =============================================================================\n\n")
    for k, v in data.items():
        lines.append(f"{k}={v}\n")

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"[+] Đã lưu cấu hình thành công vào: {filepath}")

def run_wizard():
    print("\n" + "=" * 65)
    print("   YTC PRICE ACTION TRADER - DEPLOYMENT CONFIGURATION WIZARD")
    print("=" * 65)

    # 1. Chọn chế độ triển khai
    print("\n[BƯỚC 1] Chọn chế độ triển khai:")
    print("  1. Paper Trading (Giả lập / Mô phỏng chạy offline, không cần MT5)")
    print("  2. Live Trading  (Kết nối MetaTrader 5 terminal thực tế)")
    choice_mode = input("Nhập lựa chọn [1/2, mặc định: 1]: ").strip()
    is_live = (choice_mode == "2")
    mode = "live" if is_live else "paper"
    env_file = os.path.join(BASE_DIR, f".env.{mode}")
    print(f" -> Chế độ: {mode.upper()} (File lưu trữ: .env.{mode})")

    current = load_existing_env(env_file)

    # 2. Cấu hình AI Provider & API Key
    print("\n[BƯỚC 2] Cấu hình Bộ não AI (AI Strategy Brain):")
    print("  1. Google Gemini (Khuyên dùng, phản hồi cực nhanh <1.5s, miễn phí)")
    print("  2. OpenAI (GPT-4o)")
    print("  3. Mock Engine (Offline, không cần API key)")
    choice_ai = input("Nhập nhà cung cấp AI [1/2/3, mặc định: 1]: ").strip()

    ai_provider = "gemini"
    default_model = "gemini-2.0-flash"
    if choice_ai == "2":
        ai_provider = "openai"
        default_model = "gpt-4o"
    elif choice_ai == "3":
        ai_provider = "mock"

    gemini_key = current.get("GEMINI_API_KEY", "")
    openai_key = current.get("OPENAI_API_KEY", "")

    if ai_provider == "gemini":
        prompt_key = f"Nhập Google Gemini API Key [{gemini_key[:6]}... nếu muốn giữ nguyên]: " if gemini_key else "Nhập Google Gemini API Key (lấy tại https://aistudio.google.com/): "
        input_key = input(prompt_key).strip()
        if input_key:
            gemini_key = input_key
        
        if gemini_key:
            test_gemini_key(gemini_key, default_model)
        else:
            print(" [!] Bạn chưa nhập Gemini API Key. Hệ thống sẽ tự động chạy chế độ Offline Mock Engine khi khởi động.")

    elif ai_provider == "openai":
        prompt_key = f"Nhập OpenAI API Key [{openai_key[:6]}... nếu muốn giữ nguyên]: " if openai_key else "Nhập OpenAI API Key (sk-...): "
        input_key = input(prompt_key).strip()
        if input_key:
            openai_key = input_key
        if openai_key:
            test_openai_key(openai_key, default_model)

    # 3. Model selection
    model_name = input(f"Nhập tên model AI [Mặc định: {default_model}]: ").strip() or default_model

    # 4. Symbol
    default_symbol = current.get("SYMBOL", "XAUUSD")
    symbol = input(f"\n[BƯỚC 3] Mã sản phẩm giao dịch [Mặc định: {default_symbol}]: ").strip() or default_symbol

    config_data = {
        "AI_PROVIDER": ai_provider,
        "GEMINI_API_KEY": gemini_key,
        "OPENAI_API_KEY": openai_key,
        "AI_MODEL_NAME": model_name,
        "TRADING_MODE": mode,
        "SYMBOL": symbol,
        "SERVER_A_HOST": "127.0.0.1",
        "SERVER_A_PORT": current.get("SERVER_A_PORT", "29120"),
        "SERVER_B_HOST": "127.0.0.1",
        "SERVER_B_PORT": current.get("SERVER_B_PORT", "29121"),
    }

    # 5. Nếu là live mode -> MT5 info
    if is_live:
        print("\n[BƯỚC 4] Thông tin tài khoản MetaTrader 5:")
        print(" (Lưu ý: Nếu phần mềm MT5 đã đang mở và đăng nhập sẵn trên máy, bạn có thể bỏ qua bước này)")
        login = input(f"MT5 Login ID [{current.get('MT5_LOGIN', '')}]: ").strip() or current.get("MT5_LOGIN", "")
        server = input(f"MT5 Server [{current.get('MT5_SERVER', '')}]: ").strip() or current.get("MT5_SERVER", "")
        config_data["MT5_LOGIN"] = login
        config_data["MT5_SERVER"] = server

    # Lưu file
    save_env_file(env_file, config_data)

    print("\n" + "=" * 65)
    print("   THIẾT LẬP HOÀN TẤT!")
    print(f"   - File đã lưu: .env.{mode}")
    print(f"   - Lệnh khởi chạy: python run.py --env {mode}")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    run_wizard()
