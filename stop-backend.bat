@echo off
chcp 65001 >nul
title Stop Stability Test Backend

echo ============================================
echo     Stability Test Backend Stop Script
echo ============================================
echo.

:: Find and kill process using port 8000
echo Checking port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000') do (
    echo Found process PID: %%a using port 8000
    echo Stopping process...
    taskkill /F /PID %%a 2>nul
    if errorlevel 1 (
        echo Failed to stop process %%a, trying alternative method...
        powershell -Command "Stop-Process -Id %%a -Force" 2>nul
    ) else (
        echo Process %%a stopped successfully.
    )
)

:: Double check for any python processes
echo.
echo Checking for remaining Python processes...
tasklist | findstr python.exe >nul
if errorlevel 1 (
    echo No Python processes found.
) else (
    echo Found Python processes, attempting to stop...
    taskkill /F /IM python.exe 2>nul
    echo Python processes stopped.
)

echo.
echo Verifying port 8000 is free...
timeout /t 2 /nobreak >nul
netstat -ano | findstr :8000 >nul
if errorlevel 1 (
    echo ✓ Port 8000 is now free.
) else (
    echo ! Warning: Port 8000 is still in use.
    echo You may need to restart your computer.
)

echo.
echo ============================================
echo              Stop Complete
echo ============================================
pause
