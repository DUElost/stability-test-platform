@echo off
setlocal

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

if /I "%STP_BACKEND_NORELOAD%"=="1" (
    echo Starting backend without auto-reload...
    python -m uvicorn %UVICORN_ARGS%
) else (
    echo Starting backend with auto-reload enabled...
    python -m uvicorn %UVICORN_ARGS% --reload
)

pause
