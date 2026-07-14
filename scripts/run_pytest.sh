#!/usr/bin/env bash
# 在仓库根目录运行 pytest；自动加载 .env.test（若存在）
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env.test ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env.test
  set +a
elif [[ -z "${TEST_DATABASE_URL:-}" ]]; then
  echo "hint: copy .env.test.example to .env.test or export TEST_DATABASE_URL" >&2
fi

exec "$ROOT/.venv/bin/python" -m pytest "$@"
