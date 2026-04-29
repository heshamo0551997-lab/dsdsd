@echo off
echo ====================================================
echo   TG Monitor Pro - Starter Script
echo ====================================================
echo.

if not exist venv (
    echo Error: Virtual environment not found. Run setup.bat first.
    pause
    exit /b
)

echo Starting Services...

:: Start API
start "TG Monitor - API" cmd /k "call venv\Scripts\activate && uvicorn app.api.main:app --host 0.0.0.0 --port 8000"

:: Start Bot
start "TG Monitor - Bot" cmd /k "call venv\Scripts\activate && python -m app.bot.main"

:: Start Listener
start "TG Monitor - Listener" cmd /k "call venv\Scripts\activate && python -m app.listener.main"

echo.
echo Services are starting in separate windows.
echo API: http://127.0.0.1:8000
echo.
pause
