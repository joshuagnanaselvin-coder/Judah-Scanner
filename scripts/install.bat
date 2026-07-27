@echo off
chcp 65001 >nul
echo ========================================
echo    JUDAH SCANNER - FIRST TIME SETUP
echo ========================================
echo.

REM Check Python
python --version >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python not found! Install from python.org
    pause
    exit /b 1
)

echo [1/3] Creating virtual environment...
if exist venv (
    echo   venv already exists, skipping creation.
) else (
    python -m venv venv
)

echo [2/3] Installing dependencies...
call venv\Scripts\activate.bat
pip install --upgrade pip
pip install -r requirements.txt

echo [3/3] Setup complete!
echo.
echo Run scripts\start.bat to launch.
pause
