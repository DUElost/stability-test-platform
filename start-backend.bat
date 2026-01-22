@echo off
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

REM Upgrade pip first
echo Upgrading pip...
python -m pip install --upgrade pip --quiet

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Upgrade pip in virtual environment
echo Upgrading pip in virtual environment...
python -m pip install --upgrade pip --quiet

REM Install dependencies (without bcrypt on Windows)
echo Installing Python dependencies...
echo Installing fastapi...
pip install -q fastapi
echo Installing uvicorn...
pip install -q uvicorn[standard]
echo Installing sqlalchemy...
pip install -q sqlalchemy
echo Installing pydantic...
pip install -q pydantic
echo Installing paramiko...
pip install -q paramiko
echo Installing requests...
pip install -q requests
echo Installing aiohttp...
pip install -q aiohttp
echo.
echo All dependencies installed successfully!

REM Start the server
echo.
echo Starting FastAPI server on http://localhost:8000
echo.
echo Press Ctrl+C to stop the server
echo.

uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
