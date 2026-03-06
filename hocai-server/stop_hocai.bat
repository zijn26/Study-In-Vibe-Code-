@echo off
echo   Stopping HOCAI Server...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /F /PID %%a >nul 2>&1
    echo   [OK] Server stopped (PID: %%a)
)
echo   Done.
timeout /t 2 >nul
