#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# pg_restore_test.sh — Verify a backup is restorable (DR drill)
#
# This script creates a temporary database, restores the latest backup into it,
# runs a sanity check, then drops the temporary database.
# Run periodically (e.g. monthly) to validate backup integrity.
#
# Only prints "Restore drill PASSED" when restore + sanity checks all pass:
# SQL errors (ON_ERROR_STOP), failed count queries, or too few tables exit non-zero.
#
# Usage:
#   ./pg_restore_test.sh [backup_file]
#   # If no backup_file given, uses the latest in BACKUP_DIR
#
# Environment variables:
#   PGHOST       — default 127.0.0.1
#   PGPORT       — default 5432
#   PGUSER       — default stp (must have CREATEDB privilege)
#   PGDATABASE   — default stp (the production DB name)
#   BACKUP_DIR   — default /home/debian13/stability-test-platform/backups
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-stp}"
PGDATABASE="${PGDATABASE:-stp}"
BACKUP_DIR="${BACKUP_DIR:-/home/debian13/stability-test-platform/backups}"

# Find the latest backup if not specified
if [ $# -ge 1 ]; then
  BACKUP_FILE="$1"
else
  # #826: ls 失败（无备份/目录不可读）在 set -euo pipefail 下会让赋值语句
  # 直接退出——`|| true` 兜住，让下方空值分支能打出明确报错。
  BACKUP_FILE=$(ls -t "${BACKUP_DIR}/${PGDATABASE}"_*.sql.gz 2>/dev/null | head -1 || true)
  if [ -z "${BACKUP_FILE}" ]; then
    echo "ERROR: No backup files found in ${BACKUP_DIR}" >&2
    exit 1
  fi
fi

TEST_DB="_restore_test_$(date +%Y%m%d_%H%M%S)"

echo "[$(date -Iseconds)] Restore drill starting"
echo "  Backup:  ${BACKUP_FILE}"
echo "  Test DB:  ${TEST_DB}"

# Create temporary database
psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d postgres \
  -c "CREATE DATABASE \"${TEST_DB}\";" 2>&1 || {
  echo "ERROR: Could not create test database ${TEST_DB}" >&2
  exit 1
}

cleanup() {
  echo "[$(date -Iseconds)] Cleaning up test database: ${TEST_DB}"
  psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d postgres \
    -c "DROP DATABASE IF EXISTS \"${TEST_DB}\";" 2>&1 || true
}
trap cleanup EXIT

# Restore
# ON_ERROR_STOP=1：#1255 — 中途 SQL 错误立即使 psql 非零，不得继续到 PASSED
if ! gunzip -c "${BACKUP_FILE}" | psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${TEST_DB}" -q -v ON_ERROR_STOP=1 2>&1; then
  echo "ERROR: Restore failed" >&2
  exit 1
fi

# Sanity checks
# 计数查询 stderr 不入变量；查询失败（管道非零）或输出非数字都中止演练
run_count_query() {
  psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${TEST_DB}" -t -A -c "$1" 2>/dev/null | tr -d '[:space:]'
}

TABLE_COUNT="$(run_count_query "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';")" \
  || { echo "ERROR: tables count query failed" >&2; exit 1; }
HOST_COUNT="$(run_count_query "SELECT count(*) FROM host;")" \
  || { echo "ERROR: host count query failed" >&2; exit 1; }
PLAN_COUNT="$(run_count_query "SELECT count(*) FROM plan;")" \
  || { echo "ERROR: plan count query failed" >&2; exit 1; }

check_count() {
  local label="$1" value="$2"
  case "${value}" in
    '' | *[!0-9]*)
      echo "ERROR: ${label} count query failed (got: '${value}')" >&2
      exit 1
      ;;
  esac
}
check_count tables "${TABLE_COUNT}"
check_count host "${HOST_COUNT}"
check_count plan "${PLAN_COUNT}"

echo "[$(date -Iseconds)] Restore verification:"
echo "  Tables restored: ${TABLE_COUNT}"
echo "  Host rows:       ${HOST_COUNT}"
echo "  Plan rows:       ${PLAN_COUNT}"

if [ "${TABLE_COUNT}" -lt 5 ]; then
  echo "ERROR: Only ${TABLE_COUNT} tables restored — expected at least 5" >&2
  exit 1
fi

echo "[$(date -Iseconds)] Restore drill PASSED"
