#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend"

echo "Starting Stability Test Platform - Frontend (WSL)..."
echo

if [ "${EUID}" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
  echo "ERROR: Do not run this script with sudo."
  echo "Please run: ./start-frontend-wsl.sh"
  exit 1
fi

load_nvm() {
  if [ -n "${NVM_DIR:-}" ] && [ -s "${NVM_DIR}/nvm.sh" ]; then
    # shellcheck disable=SC1090
    source "${NVM_DIR}/nvm.sh"
    return
  fi
  if [ -s "${HOME}/.nvm/nvm.sh" ]; then
    export NVM_DIR="${HOME}/.nvm"
    # shellcheck disable=SC1090
    source "${NVM_DIR}/nvm.sh"
  fi
}

node_major() {
  node -p 'process.versions.node.split(".")[0]'
}

load_nvm

if ! command -v node >/dev/null 2>&1; then
  if command -v nvm >/dev/null 2>&1; then
    echo "Node.js not found, installing Node 20 via nvm..."
    nvm install 20 >/dev/null
    nvm use 20 >/dev/null
  else
    echo "ERROR: Node.js is not installed in WSL."
    echo "Install Node 20+ (recommended: nvm) and retry."
    echo "Example:"
    echo "  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash"
    echo "  source ~/.nvm/nvm.sh && nvm install 20 && nvm use 20"
    exit 1
  fi
fi

if [ "$(node_major)" -lt 20 ]; then
  if command -v nvm >/dev/null 2>&1; then
    echo "Current Node is $(node -v), switching to Node 20 via nvm..."
    nvm install 20 >/dev/null
    nvm use 20 >/dev/null
  fi
fi

if [ "$(node_major)" -lt 20 ]; then
  echo "ERROR: Node.js version must be >= 20 (current: $(node -v))."
  echo "If you use nvm, run:"
  echo "  nvm install 20 && nvm use 20"
  exit 1
fi

cd "$FRONTEND_DIR"

if [ -d "node_modules/@esbuild/win32-x64" ]; then
  echo "Detected Windows node_modules in WSL, reinstalling dependencies for Linux..."
  rm -rf node_modules
fi

if [ ! -d "node_modules" ]; then
  echo "Installing Node.js dependencies..."
  npm install
fi

echo
echo "Starting Vite dev server on http://localhost:5173"
echo "Press Ctrl+C to stop the server."
echo

exec npm run dev -- --host 0.0.0.0 --port 5173
