@echo off
title HOCAI Server - Stop
color 0C

echo.
echo   =========================================
echo     HOCAI  -  Stopping Server
echo   =========================================
echo.

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /F /PID %%a >nul 2>&1
    echo   [OK] Server stopped (PID: %%a)
)

echo   [INFO] Done.
echo.
timeout /t 2 >nul
