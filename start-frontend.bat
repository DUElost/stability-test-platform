@echo off
echo Starting Stability Test Platform - Frontend...
echo.

cd /d "%~dp0frontend"

REM Check if Node.js is installed
node --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Node.js is not installed or not in PATH
    echo Please install Node.js 16+ and add to PATH
    pause
    exit /b 1
)

REM Install dependencies if needed
if not exist "node_modules" (
    echo Installing Node.js dependencies...
    npm install
)

REM Start the dev server
echo.
echo Starting Vite dev server on http://localhost:5173
echo.
echo Press Ctrl+C to stop the server
echo.

npm run dev
