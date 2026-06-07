@echo off
setlocal

cd /d "%~dp0"
set "STP_FRONTEND_STOP_ROOT=%cd%"

echo ============================================
echo    Stability Test Frontend Stop Script
echo ============================================
echo.

echo Checking Windows frontend Vite processes...

set "WINDOWS_FOUND="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command ^
  "$root = [System.IO.Path]::GetFullPath($env:STP_FRONTEND_STOP_ROOT).ToLowerInvariant();" ^
  "$names = @('node','node.exe','npm','npm.exe','pnpm','pnpm.exe','yarn','yarn.exe');" ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $names.Contains($_.Name.ToLowerInvariant()) -and $_.CommandLine.ToLowerInvariant().Contains('vite') -and $_.CommandLine.ToLowerInvariant().Contains($root) } | Select-Object -ExpandProperty ProcessId"`) do (
    set "WINDOWS_FOUND=1"
    echo Stopping Windows frontend PID %%p...
    taskkill /PID %%p /F /T >nul 2>&1
    if errorlevel 1 (
        echo Warning: failed to stop Windows frontend PID %%p.
    )
)

if not defined WINDOWS_FOUND (
    echo No Windows frontend Vite process found.
)

echo.
echo Checking WSL frontend processes...

where wsl >nul 2>&1
if errorlevel 1 (
    echo WSL not found, skipping WSL cleanup.
    goto verify_frontend
)

for /f "delims=" %%i in ('wsl wslpath -a "%cd%"') do set "WSL_PROJECT_DIR=%%i"

if not defined WSL_PROJECT_DIR (
    echo Warning: Failed to resolve WSL path, skipping WSL cleanup.
    goto verify_frontend
)

set "WSL_CMD=cd '%WSL_PROJECT_DIR%' && chmod +x ./stop-frontend-wsl.sh && ./stop-frontend-wsl.sh"

if defined WSL_DISTRO (
    wsl -d "%WSL_DISTRO%" -e bash -lc "%WSL_CMD%"
) else (
    wsl -e bash -lc "%WSL_CMD%"
)

:verify_frontend
timeout /t 1 /nobreak >nul 2>&1

set "STILL_RUNNING="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command ^
  "$root = [System.IO.Path]::GetFullPath($env:STP_FRONTEND_STOP_ROOT).ToLowerInvariant();" ^
  "$names = @('node','node.exe','npm','npm.exe','pnpm','pnpm.exe','yarn','yarn.exe');" ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $names.Contains($_.Name.ToLowerInvariant()) -and $_.CommandLine.ToLowerInvariant().Contains('vite') -and $_.CommandLine.ToLowerInvariant().Contains($root) } | Select-Object -ExpandProperty ProcessId"`) do (
    set "STILL_RUNNING=1"
    echo Frontend PID %%p is still running.
)

if defined STILL_RUNNING (
    echo.
    echo ERROR: Some frontend Vite processes are still running.
    exit /b 1
)

echo.
echo Repo frontend Vite processes are now stopped.
echo Stop complete.
exit /b 0
