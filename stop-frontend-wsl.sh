#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================"
echo "   Stability Test Frontend Stop Script (WSL)"
echo "============================================"
echo

escaped_root="$(printf '%s' "$ROOT_DIR" | sed 's/[][(){}.^$*+?|\\]/\\&/g')"

echo "Checking repo frontend Vite processes..."

pkill -f "${escaped_root}/frontend/.*vite" >/dev/null 2>&1 || true
pkill -f "${escaped_root}/\\.worktrees/.*/frontend/.*vite" >/dev/null 2>&1 || true
pkill -f "vite.*--host 0\\.0\\.0\\.0.*--port 5173" >/dev/null 2>&1 || true

sleep 1

if pgrep -af "${escaped_root}.*vite" >/dev/null 2>&1; then
  echo "ERROR: Some WSL frontend Vite processes are still running:"
  pgrep -af "${escaped_root}.*vite" || true
  exit 1
fi

echo "Repo frontend Vite processes are now stopped."
echo "Stop complete."
