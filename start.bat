@echo off
setlocal EnableDelayedExpansion

:: Dam bao thu muc lam viec luon la thu muc chua file start.bat nay
cd /d "%~dp0"

title YTC Price Action Trading System

:: Kiem tra Python tren he thong
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo [LOI NGHIEP TRONG] Khong tim thay lenh 'python' trong PATH he thong!
    echo Vui long cai dat Python 3.10 tro len va tich vao 'Add python.exe to PATH'.
    echo.
    pause
    exit /b 1
)

echo ======================================================================
echo   YTC PRICE ACTION TRADER - KIEM TRA PORT VA KHOI CHAY
echo ======================================================================

:: Kiem tra va giai phong port 29120 (Server A)
for /f "tokens=5" %%a in ('netstat -ano -p tcp ^| findstr ":29120 "') do (
    if not "%%a"=="" if not "%%a"=="0" (
        echo [CANH BAO] Port 29120 dang bi chiem dung boi PID: %%a. Dang giai phong...
        taskkill /F /PID %%a >nul 2>&1
        echo [OK] Da dong tien trinh %%a tren port 29120.
    )
)

:: Kiem tra va giai phong port 29121 (Server B)
for /f "tokens=5" %%a in ('netstat -ano -p tcp ^| findstr ":29121 "') do (
    if not "%%a"=="" if not "%%a"=="0" (
        echo [CANH BAO] Port 29121 dang bi chiem dung boi PID: %%a. Dang giai phong...
        taskkill /F /PID %%a >nul 2>&1
        echo [OK] Da dong tien trinh %%a tren port 29121.
    )
)

echo [OK] Cac cong mang 29120 va 29121 da san sang.
echo.

if "%1"=="paper" goto RUN_PAPER
if "%1"=="live" goto RUN_LIVE
if "%1"=="check" goto RUN_CHECK
if "%1"=="setup" goto RUN_SETUP
if "%1"=="test" goto RUN_TEST

:MENU
echo ======================================================================
echo   CHON CHE DO CHAY DU AN (Uu tien Live MT5):
echo ======================================================================
echo   1. Che do Giao dich That Live MT5 (Khuyen dung - Ket noi MetaTrader 5)
echo   2. Kiem tra Trang thai Tu dong Giao dich MT5 (AlgoTrading Check)
echo   3. Che do Mo phong (Paper Trading - Mo phong ao)
echo   4. Thiet lap Cau hinh va API Keys (Setup Wizard)
echo   5. Chay Kiem tra Don vi (Unit Tests)
echo   6. Thoat
echo ======================================================================
set /p CHOICE="Nhap lua chon [1-6, mac dinh la 1]: "
if "!CHOICE!"=="" set CHOICE=1

if "%CHOICE%"=="1" goto RUN_LIVE
if "%CHOICE%"=="2" goto RUN_CHECK
if "%CHOICE%"=="3" goto RUN_PAPER
if "%CHOICE%"=="4" goto RUN_SETUP
if "%CHOICE%"=="5" goto RUN_TEST
if "%CHOICE%"=="6" goto EXIT_PROG

echo.
echo [LOI] Lua chon khong hop le. Vui long chon tu 1 den 6.
echo.
goto MENU

:RUN_PAPER
echo.
echo [KHOI DONG] Dang chay che do Mo phong (Paper Trading)...
echo [DASHBOARD] Trinh duyet se tu dong mo tai: http://127.0.0.1:29120
start "" "http://127.0.0.1:29120"
python run.py --env paper
if errorlevel 1 (
    echo.
    echo [LOI] He thong bi dung lai dot ngot.
    pause
)
goto END

:RUN_LIVE
echo.
set LIVE_SYM=XAUUSD
if "%1"=="" (
    set /p LIVE_SYM="Nhap ma san pham giao dich [Mac dinh: XAUUSD]: "
    if "!LIVE_SYM!"=="" set LIVE_SYM=XAUUSD
)
echo [KHOI DONG] Dang chay che do Live MT5 voi ma !LIVE_SYM!...
echo [DASHBOARD] Trinh duyet se tu dong mo tai: http://127.0.0.1:29120
start "" "http://127.0.0.1:29120"
python run.py --env live --symbol !LIVE_SYM!
if errorlevel 1 (
    echo.
    echo [LOI] He thong bi dung lai dot ngot.
    pause
)
goto END

:RUN_CHECK
echo.
python check_mt5_status.py
echo.
if not "%1"=="" goto END
pause
goto MENU

:RUN_SETUP
echo.
python setup_env.py
echo.
if not "%1"=="" goto END
goto MENU

:RUN_TEST
echo.
python -m unittest tests/test_system.py
echo.
if not "%1"=="" goto END
pause
goto MENU

:EXIT_PROG
echo Da thoat chuong trinh.
exit /b 0

:END
endlocal
