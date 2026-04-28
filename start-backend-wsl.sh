#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
VENV_DIR="${ROOT_DIR}/venv-wsl"
BACKEND_PORT="${1:-${BACKEND_PORT:-8000}}"

echo "Starting Stability Test Platform - Backend (WSL)..."
echo

if [ "${EUID}" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
  echo "ERROR: Do not run this script with sudo."
  echo "Please run: ./start-backend-wsl.sh"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is not installed in WSL."
  echo "Please install Python 3.10+ in your WSL distribution."
  exit 1
fi

if [ ! -f "${VENV_DIR}/bin/activate" ]; then
  echo "Creating Linux virtual environment at ${VENV_DIR}..."
  mkdir -p "${VENV_DIR}"
  python3 -m venv --clear "${VENV_DIR}"
  new_venv=1
else
  new_venv=0
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

missing_deps=0
python - <<'PY' >/dev/null 2>&1 || missing_deps=1
import fastapi
import uvicorn
import sqlalchemy
import pydantic
import requests
import multipart
PY

if [ "$new_venv" -eq 1 ] || [ "$missing_deps" -eq 1 ]; then
  echo "Installing backend dependencies..."
  if [ -f "backend/requirements.txt" ]; then
    python -m pip install -r backend/requirements.txt
  else
    python -m pip install fastapi "uvicorn[standard]" sqlalchemy pydantic requests python-multipart
  fi
fi

echo
echo "Starting FastAPI server on http://localhost:${BACKEND_PORT}"
echo "Press Ctrl+C to stop the server."
echo

if [ "${STP_BACKEND_NORELOAD:-}" = "1" ]; then
  echo "Auto-reload disabled."
  exec uvicorn backend.main:app --host 0.0.0.0 --port "$BACKEND_PORT"
fi

echo "Auto-reload enabled."
exec uvicorn backend.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload
