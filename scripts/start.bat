@echo off
chcp 65001 >nul
title Judah Scanner (Dashboard + Excel Logger)
cd /d "%~dp0.."

REM ============================================
REM  Starts: Scanner server + Web UI + Excel logger
REM  Excel output: signal_log.xlsx (auto-created)
REM  Analysis: python audit.py -> quality_report.html
REM ============================================

REM Check venv exists
if not exist venv\Scripts\python.exe (
    echo [ERROR] Virtual environment not found. Run scripts\install.bat first.
    pause
    exit /b 1
)

echo Starting Judah Scanner...
echo Dashboard: http://localhost:8000
echo Excel logger: signal_log.xlsx
echo Audit report: quality_report.html (run: python audit.py)
echo Press Ctrl+C to stop
echo.

call venv\Scripts\activate.bat

REM Start signal logger in a separate window
start "Judah Excel Logger" cmd /c "cd /d "%~dp0.." && call venv\Scripts\activate.bat && python signal_logger.py"

REM Wait for logger to create the Excel file, then open it
timeout /t 3 /nobreak >nul
if exist signal_log.xlsx (
    start "" signal_log.xlsx
    echo [Excel] Opened signal_log.xlsx
)

REM Open dashboard in browser
timeout /t 2 /nobreak >nul
start http://localhost:8000

REM Start the scanner (main window)
python -m backend.main
pause
