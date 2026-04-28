@echo off
setlocal

cd /d "%~dp0"

if not "%~1"=="" set "BACKEND_PORT=%~1"
if not defined BACKEND_PORT set "BACKEND_PORT=8000"

echo ============================================
echo    Stability Test Backend Stop Script
echo ============================================
echo.

echo Checking Windows port %BACKEND_PORT%...

set "WINDOWS_PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%BACKEND_PORT% .*LISTENING"') do (
    set "WINDOWS_PID=%%p"
    echo Stopping Windows process PID %%p on port %BACKEND_PORT%...
    taskkill /PID %%p /F /T
    if errorlevel 1 (
        echo Warning: failed to stop Windows process PID %%p.
    )
)

echo.
echo Checking WSL port %BACKEND_PORT%...

where wsl >nul 2>&1
if errorlevel 1 (
    echo WSL not found, skipping WSL cleanup.
    goto verify_windows_port
)

for /f "delims=" %%i in ('wsl wslpath -a "%cd%"') do set "WSL_PROJECT_DIR=%%i"

if not defined WSL_PROJECT_DIR (
    echo Warning: Failed to resolve WSL path, skipping WSL cleanup.
    goto verify_windows_port
)

set "WSL_CMD=cd '%WSL_PROJECT_DIR%' && chmod +x ./stop-backend-wsl.sh && ./stop-backend-wsl.sh '%BACKEND_PORT%'"

if defined WSL_DISTRO (
    wsl -d "%WSL_DISTRO%" -e bash -lc "%WSL_CMD%"
) else (
    wsl -e bash -lc "%WSL_CMD%"
)

:verify_windows_port
timeout /t 1 /nobreak >nul 2>&1

set "STILL_IN_USE_PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%BACKEND_PORT% .*LISTENING"') do (
    set "STILL_IN_USE_PID=%%p"
)

if defined STILL_IN_USE_PID (
    echo.
    echo ERROR: Windows port %BACKEND_PORT% is still in use by PID %STILL_IN_USE_PID%.
    exit /b 1
)

echo.
echo Windows port %BACKEND_PORT% is now free.
echo Stop complete.
