@echo off

REM Windows Native Frontend Launcher for Stability Test Platform

cd /d "%~dp0\frontend"

if not "%~1"=="" set "BACKEND_PORT=%~1"

echo Starting Stability Test Platform - Frontend (Windows)...
echo.

REM Check Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js is not installed or not in PATH.
    echo Please install Node.js 20+ from https://nodejs.org/
    pause
    exit /b 1
)

echo Node.js version check passed.

REM Determine package manager
if exist "pnpm-lock.yaml" (
    echo Package manager: pnpm
    set PKG_CMD=pnpm install
) else (
    echo Package manager: npm
    set PKG_CMD=npm install
)

REM Check if dependencies need to be installed
if not exist "node_modules" (
    echo.
    echo Installing dependencies...
    %PKG_CMD%
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies
        pause
        exit /b 1
    )
)

REM Verify vite is available
if not exist "node_modules\.bin\vite.cmd" (
    echo.
    echo Vite not found, reinstalling dependencies...
    rmdir /s /q node_modules 2>nul
    %PKG_CMD%
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies
        pause
        exit /b 1
    )
)

echo.
echo Starting Vite dev server on http://localhost:5173

if not defined BACKEND_PORT set "BACKEND_PORT=8000"
if not defined VITE_API_BASE_URL set "VITE_API_BASE_URL=http://localhost:%BACKEND_PORT%"
if not defined VITE_WS_BASE_URL set "VITE_WS_BASE_URL=ws://localhost:%BACKEND_PORT%"

echo Backend API target: %VITE_API_BASE_URL%
echo Press Ctrl+C to stop the server.
echo.

REM Start Vite dev server
npm run dev -- --host 0.0.0.0 --port 5173

pause
