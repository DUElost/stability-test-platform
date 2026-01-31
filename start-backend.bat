@echo off
setlocal enabledelayedexpansion

echo Starting Stability Test Platform - Backend Service...
echo.

cd /d "%~dp0"

REM Check if Python is installed
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8+ and add to PATH
    pause
    exit /b 1
)

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    set "NEW_VENV=1"
) else (
    set "NEW_VENV=0"
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Define required packages
set "PACKAGES=fastapi uvicorn sqlalchemy pydantic requests python-multipart"
set "MISSING_PACKAGES="

REM Check if any package is missing (skip check if venv was just created)
if "%NEW_VENV%"=="1" (
    set "MISSING_PACKAGES=%PACKAGES%"
) else (
    echo Checking dependencies...
    for %%p in (%PACKAGES%) do (
        pip show %%p >nul 2>&1
        if !ERRORLEVEL! neq 0 (
            if "!MISSING_PACKAGES!"=="" (
                set "MISSING_PACKAGES=%%p"
            ) else (
                set "MISSING_PACKAGES=!MISSING_PACKAGES! %%p"
            )
        )
    )
)

REM Install only missing packages
if not "!MISSING_PACKAGES!"=="" (
    echo.
    echo Installing missing dependencies: !MISSING_PACKAGES!
    for %%p in (!MISSING_PACKAGES!) do (
        echo   - Installing %%p...
        if "%%p"=="uvicorn" (
            pip install -q uvicorn[standard]
        ) else (
            pip install -q %%p
        )
    )
    echo.
    echo All dependencies installed successfully!
) else (
    echo All dependencies are already installed. Skipping installation.
)

REM Start the server
echo.
echo Starting FastAPI server on http://localhost:8000
echo.
echo Press Ctrl+C to stop the server
echo.

uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
