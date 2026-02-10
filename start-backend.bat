@echo off
setlocal

cd /d "%~dp0"

for /f "delims=" %%i in ('wsl wslpath -a "%cd%"') do set "WSL_PROJECT_DIR=%%i"

if not defined WSL_PROJECT_DIR (
    echo ERROR: Failed to resolve WSL path.
    exit /b 1
)

set "WSL_CMD=cd '%WSL_PROJECT_DIR%' && chmod +x ./start-backend-wsl.sh && ./start-backend-wsl.sh"

if defined WSL_DISTRO (
    wsl -d "%WSL_DISTRO%" -e bash -lc "%WSL_CMD%"
) else (
    wsl -e bash -lc "%WSL_CMD%"
)
