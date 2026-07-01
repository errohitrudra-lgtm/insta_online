@echo off
REM ============================================================
REM  Instagram Reel Monitor – Setup & Run Script
REM  Downloads reels from target accounts automatically.
REM  Run this once – it sets up venv, installs deps, and starts.
REM ============================================================

title Instagram Reel Monitor
cd /d "%~dp0"

echo.
echo ============================================================
echo   Instagram Reel Monitor - Setup ^& Launch
echo ============================================================
echo.

REM --- Check Python ---
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo         Download from https://python.org/downloads
    pause
    exit /b 1
)

REM --- Show Python version ---
echo [1/5] Checking Python...
python --version

REM --- Create virtual environment if not exists ---
if not exist ".venv" (
    echo.
    echo [2/5] Creating virtual environment...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create venv. Check your Python installation.
        pause
        exit /b 1
    )
    echo       Done.
) else (
    echo [2/5] Virtual environment already exists.
)

REM --- Activate venv ---
call .venv\Scripts\activate.bat

REM --- Install / upgrade dependencies ---
echo.
echo [3/5] Installing dependencies...
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo       Done.

REM --- Install Playwright browsers ---
echo.
echo [4/5] Installing Playwright browsers (Chromium)...
python -m playwright install chromium --with-deps 2>nul
if %errorlevel% neq 0 (
    echo       Playwright browser install had warnings, trying alternative...
    python -m playwright install chromium 2>nul
)
echo       Done.

REM --- Create directories ---
if not exist "storage" mkdir storage
if not exist "logs" mkdir logs

REM --- Check config ---
if not exist "config.json" (
    echo.
    echo [ERROR] config.json not found!
    echo         Edit config.json with your settings, then run this script again.
    pause
    exit /b 1
)

REM --- Launch ---
echo.
echo [5/5] Starting Instagram Reel Monitor...
echo.
echo   Dashboard: http://127.0.0.1:8000
echo   Downloads: .\storage\
echo   Logs:      .\logs\app.log
echo   Auto-Upload: ENABLED
echo   Schedule:    ENABLED
echo   Retry:       ENABLED
echo.
echo   Press Ctrl+C to stop.
echo ============================================================
echo.

REM --- Kill any old instance on port 8000 ---
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "127.0.0.1:8000" ^| findstr "LISTENING"') do (
    echo   Killing old process on port 8000 ^(PID %%a^)...
    taskkill /F /PID %%a >nul 2>&1
    timeout /t 2 /nobreak >nul
)

:loop
python -m src run --config config.json

REM --- If we get here, the app crashed/exited ---
echo.
echo [%date% %time%] Monitor stopped unexpectedly. Restarting in 10 seconds...
echo   Press Ctrl+C to exit permanently.
timeout /t 10 /nobreak
goto loop
