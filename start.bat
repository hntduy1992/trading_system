@echo off
setlocal EnableDelayedExpansion

:: 1. DAM BAO SYSTEM32 LUON CO TRONG PATH (Khac phuc loi thieu 'where', 'netstat', 'cmd')
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0\;%PATH%"

:: 2. Dam bao thu muc lam viec luon la thu muc chua file start.bat nay
cd /d "%~dp0"

title YTC Price Action Trading System - XAUUSD and Multi-Asset

:: 3. TIM KIEM PYTHON THONG MINH DA TANG (Smart Multi-tier Python Detector)
set "PYTHON_CMD="

:: Tầng 1: Kiểm tra python trong PATH
python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto PYTHON_FOUND
)

:: Tầng 2: Kiểm tra launcher 'py'
py --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py"
    goto PYTHON_FOUND
)

:: Tầng 3: Quét các thư mục cài đặt phổ biến trên Windows
for %%p in (
    "%LocalAppData%\Programs\Python\Python313\python.exe"
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%LocalAppData%\Programs\Python\Python310\python.exe"
    "C:\Program Files\Python313\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
) do (
    if exist %%p (
        set "PYTHON_CMD=%%~p"
        for %%d in (%%p) do set "PATH=%%~dpd;%%~dpdScripts;!PATH!"
        goto PYTHON_FOUND
    )
)

:PYTHON_NOT_FOUND
echo.
echo ======================================================================
echo   [LOI NGHIEP TRONG] KHONG TIM THAY PYTHON TREN HE THONG!
echo ======================================================================
echo He thong da quet PATH va cac thu muc cai dat pho bien nhung khong thay.
echo Vui long cai dat Python 3.10 tro len tai: https://www.python.org/downloads/
echo LUU Y: Khi cai dat, nho tich vao o 'Add python.exe to PATH'.
echo.
pause
exit /b 1

:PYTHON_FOUND
echo [OK] Phat hien Python: !PYTHON_CMD!

:: 4. KIEM TRA VA TU DONG KHOI TAO FILE .ENV NEU CHUA CO
if not exist ".env.paper" (
    if exist ".env.paper.example" (
        copy /y ".env.paper.example" ".env.paper" >nul
        echo [INIT] Da tu dong tao file .env.paper tu file mau.
    )
)
if not exist ".env.live" (
    if exist ".env.example" (
        copy /y ".env.example" ".env.live" >nul
        echo [INIT] Da tu dong tao file .env.live tu file mau.
    )
)

echo.
echo ======================================================================
echo   YTC PRICE ACTION TRADER - KIEM TRA PORT VA KHOI CHAY
echo ======================================================================

:: 5. Kiem tra va giai phong port 29120 (Server A) an toan
for /f "tokens=5" %%a in ('netstat -ano -p tcp ^| findstr ":29120 "') do (
    if not "%%a"=="" if not "%%a"=="0" (
        echo [CANH BAO] Port 29120 dang bi chiem dung boi PID: %%a. Dang giai phong...
        taskkill /F /PID %%a >nul 2>&1
        echo [OK] Da dong tien trinh %%a tren port 29120.
    )
)

:: Kiem tra va giai phong port 29121 (Server B) an toan
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
if "%1"=="news" goto RUN_NEWS
if "%1"=="setup" goto RUN_SETUP
if "%1"=="test" goto RUN_TEST

:MENU
echo ======================================================================
echo   CHON CHE DO CHAY HE THONG (Uu tien Live MT5):
echo ======================================================================
echo   1. Che do Giao dich That Live MT5 (Khuyen dung - Ket noi MetaTrader 5)
echo   2. Che do Mo phong (Paper Trading - Chay thu khong can MT5)
echo   3. Kiem tra Trang thai Ket noi MT5 (AlgoTrading & Symbol Check)
echo   4. Quet Tin tuc Kinh te & Nhan dinh Vi mo Vang (ForexFactory & AI)
echo   5. Thiet lap Cau hinh & API Keys (Setup Wizard)
echo   6. Chay Kiem tra Don vi (Run All Unit Tests)
echo   7. Thoat
echo ======================================================================
set /p CHOICE="Nhap lua chon [1-7, mac dinh la 1]: "
if "!CHOICE!"=="" set CHOICE=1

if "%CHOICE%"=="1" goto RUN_LIVE
if "%CHOICE%"=="2" goto RUN_PAPER
if "%CHOICE%"=="3" goto RUN_CHECK
if "%CHOICE%"=="4" goto RUN_NEWS
if "%CHOICE%"=="5" goto RUN_SETUP
if "%CHOICE%"=="6" goto RUN_TEST
if "%CHOICE%"=="7" goto EXIT_PROG

echo.
echo [LOI] Lua chon khong hop le. Vui long chon tu 1 den 7.
echo.
goto MENU

:RUN_LIVE
echo.
set LIVE_SYM=XAUUSD
if "%1"=="" (
    set /p LIVE_SYM="Nhap ma san pham giao dich [Mac dinh: XAUUSD]: "
    if "!LIVE_SYM!"=="" set LIVE_SYM=XAUUSD
)
echo [KHOI DONG] Dang khoi chay che do Live MT5 voi ma !LIVE_SYM!...
echo [DASHBOARD] Trinh duyet se tu dong mo tai: http://127.0.0.1:29120
start "" "http://127.0.0.1:29120"
!PYTHON_CMD! run.py --env live --symbol !LIVE_SYM!
if errorlevel 1 (
    echo.
    echo [LOI] He thong dung lai vi co loi. Xem thong tin tren de xu ly.
    pause
)
goto END

:RUN_PAPER
echo.
echo [KHOI DONG] Dang chay che do Mo phong (Paper Trading)...
echo [DASHBOARD] Trinh duyet se tu dong mo tai: http://127.0.0.1:29120
start "" "http://127.0.0.1:29120"
!PYTHON_CMD! run.py --env paper
if errorlevel 1 (
    echo.
    echo [LOI] He thong dung lai vi co loi. Xem thong tin tren de xu ly.
    pause
)
goto END

:RUN_CHECK
echo.
echo [KIEM TRA] Dang kiem tra ket noi phan mem MetaTrader 5...
!PYTHON_CMD! check_mt5_status.py
echo.
if not "%1"=="" goto END
pause
goto MENU

:RUN_NEWS
echo.
echo [TIN TUC] Dang quet lich kinh te ForexFactory va nhan dinh vi mo...
!PYTHON_CMD! -c "from infrastructure.news.news_fetcher import NewsFetcher; f = NewsFetcher(); cal = f.fetch_economic_calendar(); print('Lich kinh te USD:'); [print(' -', c['datetime_str'], '|', c['impact'], '|', c['title']) for c in cal[:6]];"
echo.
if not "%1"=="" goto END
pause
goto MENU

:RUN_SETUP
echo.
if exist "setup_env.py" (
    !PYTHON_CMD! setup_env.py
) else (
    echo [THONG BAO] File setup_env.py khong ton tai. Ban co the sua file .env.live truc tiep.
)
echo.
if not "%1"=="" goto END
goto MENU

:RUN_TEST
echo.
echo [TEST] Dang chay kiem tra toan bo 40 unit test cua he thong...
!PYTHON_CMD! -m unittest discover tests
echo.
if not "%1"=="" goto END
pause
goto MENU

:EXIT_PROG
echo Da thoat chuong trinh.
exit /b 0

:END
endlocal
