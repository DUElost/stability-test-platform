@echo off
setlocal
cd /d "%~dp0"

if not "%~1"=="" set "BACKEND_PORT=%~1"
if not defined BACKEND_PORT set "BACKEND_PORT=8000"
set "BACKEND_PID="

for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%BACKEND_PORT% .*LISTENING"') do (
    set "BACKEND_PID=%%p"
    goto port_in_use
)

goto start_backend

:port_in_use
echo ERROR: Port %BACKEND_PORT% is already in use by PID %BACKEND_PID%.
echo Run .\stop-backend.bat or stop the existing backend process first.
exit /b 1

:start_backend
echo Preparing local backend env file...
python tools\prepare_env.py --template backend\.env.example --target backend\.env
if errorlevel 1 (
    echo ERROR: Failed to prepare backend\.env from backend\.env.example.
    pause
    exit /b 1
)
python tools\ensure_backend_dev_secrets.py --env-file backend\.env
if errorlevel 1 (
    echo ERROR: Failed to ensure backend\.env has a valid AGENT_SECRET.
    pause
    exit /b 1
)

echo Running alembic migrations (upgrade head)...
pushd "%~dp0backend"
python -m alembic upgrade head
set "MIGRATE_RC=%ERRORLEVEL%"
popd
if not "%MIGRATE_RC%"=="0" (
    echo ERROR: Alembic migration failed ^(rc=%MIGRATE_RC%^). Backend NOT started.
    pause
    exit /b 1
)

set "UVICORN_ARGS=backend.main:app --host 0.0.0.0 --port %BACKEND_PORT%"

echo [SAFE] Real-device mode: starting backend without auto-reload.
python -m uvicorn %UVICORN_ARGS%

pause
