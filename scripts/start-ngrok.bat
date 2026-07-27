@echo off
chcp 65001 >nul
title Judah Scanner + Ngrok (Dashboard + Excel Logger)
cd /d "%~dp0.."

REM ============================================
REM  Starts: Scanner + Web UI + Excel logger + Ngrok
REM  Excel output: signal_log.xlsx (auto-created)
REM  Analysis: python audit.py -> quality_report.html
REM ============================================

REM Check venv exists
if not exist venv\Scripts\python.exe (
    echo [ERROR] Virtual environment not found. Run scripts\install.bat first.
    pause
    exit /b 1
)

echo [1/4] Starting Judah Scanner...
start "Judah Scanner" cmd /c "cd /d "%~dp0.." && call venv\Scripts\activate.bat && python -m backend.main"

echo [2/4] Starting Excel logger...
start "Judah Excel Logger" cmd /c "cd /d "%~dp0.." && call venv\Scripts\activate.bat && python signal_logger.py"

REM Wait for logger to create Excel file, then open it
timeout /t 3 /nobreak >nul
if exist signal_log.xlsx (
    start "" signal_log.xlsx
    echo [Excel] Opened signal_log.xlsx
)

timeout /t 3 /nobreak >nul

echo [3/4] Starting ngrok...
where ngrok >nul 2>nul
if %errorlevel% neq 0 (
    echo [WARN] ngrok not found in PATH. Install from ngrok.com
    echo       Opening dashboard at http://localhost:8000 instead.
    start http://localhost:8000
) else (
    start http://localhost:8000
    start "Judah Ngrok" cmd /c "ngrok http 8000"
)

timeout /t 3 /nobreak >nul
echo [4/4] Done!
echo.
echo ========================================
echo  Judah Scanner is running!
echo   1. Dashboard: http://localhost:8000
echo   2. Excel: signal_log.xlsx (auto-updating)
echo   3. Audit: python audit.py -> quality_report.html
echo   4. Ngrok dashboard (if running): http://localhost:4040
echo ========================================
pause
