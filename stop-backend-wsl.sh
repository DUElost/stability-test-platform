#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8000}"

echo "============================================"
echo "   Stability Test Backend Stop Script (WSL)"
echo "============================================"
echo

echo "Checking port ${PORT}..."

killed=0

if command -v fuser >/dev/null 2>&1; then
  if fuser "${PORT}/tcp" >/dev/null 2>&1; then
    fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
    killed=1
  fi
fi

if [ "$killed" -eq 0 ] && command -v lsof >/dev/null 2>&1; then
  pids="$(lsof -t -iTCP:${PORT} -sTCP:LISTEN || true)"
  if [ -n "$pids" ]; then
    echo "$pids" | xargs -r kill -TERM || true
    killed=1
  fi
fi

# Fallback: stop matching uvicorn backend process.
pkill -f "uvicorn backend.main:app" >/dev/null 2>&1 || true

sleep 1

if command -v ss >/dev/null 2>&1 && ss -ltn "sport = :${PORT}" | grep -q LISTEN; then
  echo "Port ${PORT} is still in use, sending SIGKILL..."
  if command -v lsof >/dev/null 2>&1; then
    lsof -t -iTCP:${PORT} -sTCP:LISTEN | xargs -r kill -KILL || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
  fi
fi

echo
if command -v ss >/dev/null 2>&1 && ss -ltn "sport = :${PORT}" | grep -q LISTEN; then
  echo "Warning: port ${PORT} is still in use."
  exit 1
fi

echo "Port ${PORT} is now free."
echo "Stop complete."
