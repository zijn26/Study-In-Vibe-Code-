@echo off
title HOCAI Server v2.1
color 0A

echo.
echo   =========================================
echo     HOCAI  -  Knowledge Tracking System
echo     Version 2.1
echo   =========================================
echo.

cd /d "%~dp0"

REM --- Check Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Python not found!
    echo   Please install Python 3.11+
    pause
    exit /b 1
)

REM --- Setup venv on first run ---
if not exist "venv\Scripts\activate.bat" (
    echo   [SETUP] First run - creating virtual environment...
    python -m venv venv
    echo   [SETUP] Activating and installing dependencies...
    call venv\Scripts\activate.bat
    pip install -r requirements.txt --quiet
    echo   [SETUP] Done!
    echo.
)

REM --- Always activate venv ---
echo   [INFO] Activating virtual environment...
call venv\Scripts\activate.bat

REM --- Check .env ---
if not exist ".env" (
    echo   [WARN] .env not found!
    echo   Creating from .env.example...
    copy .env.example .env >nul
    echo.
    echo   !! Please edit .env with your AI API settings !!
    echo   Opening .env in notepad...
    notepad .env
    echo.
    echo   After saving .env, press any key to start server...
    pause >nul
)

REM --- Kill old instance if running ---
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo   [OK] Starting HOCAI Server...
echo   [OK] Dashboard: http://localhost:8000
echo   [OK] API Docs:  http://localhost:8000/docs
echo.
echo   Press Ctrl+C to stop the server
echo   =========================================
echo.

REM --- Start server using venv python explicitly ---
"venv\Scripts\python.exe" main.py
