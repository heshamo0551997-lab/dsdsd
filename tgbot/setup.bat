@echo off
echo ====================================================
echo   TG Monitor Pro - One-Click Installer (Python)
echo ====================================================
echo.

echo [1/4] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python is not installed or not in PATH.
    pause
    exit /b
)

echo [2/4] Creating virtual environment...
python -m venv venv
if %errorlevel% neq 0 (
    echo Error: Failed to create virtual environment.
    pause
    exit /b
)

echo [3/4] Installing dependencies...
call venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo Error: Failed to install dependencies.
    pause
    exit /b
)

echo [4/4] Initializing database...
python -m app.core.init_db
if %errorlevel% neq 0 (
    echo Warning: Database initialization failed. Check your PostgreSQL connection.
)

echo.
echo ====================================================
echo   Installation Complete! 
echo   Use start_all.bat to launch the system.
echo ====================================================
pause
