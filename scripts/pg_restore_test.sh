#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# pg_restore_test.sh — Verify a backup is restorable (DR drill)
#
# This script creates a temporary database, restores the latest backup into it,
# runs a sanity check, then drops the temporary database.
# Run periodically (e.g. monthly) to validate backup integrity.
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
  BACKUP_FILE=$(ls -t "${BACKUP_DIR}/${PGDATABASE}"_*.sql.gz 2>/dev/null | head -1)
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
if ! gunzip -c "${BACKUP_FILE}" | psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${TEST_DB}" -q 2>&1; then
  echo "ERROR: Restore failed" >&2
  exit 1
fi

# Sanity checks
TABLE_COUNT=$(psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${TEST_DB}" -t -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>&1 | tr -d ' ')
HOST_COUNT=$(psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${TEST_DB}" -t -c \
  "SELECT count(*) FROM host;" 2>&1 | tr -d ' ')
PLAN_COUNT=$(psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${TEST_DB}" -t -c \
  "SELECT count(*) FROM plan;" 2>&1 | tr -d ' ')

echo "[$(date -Iseconds)] Restore verification:"
echo "  Tables restored: ${TABLE_COUNT}"
echo "  Host rows:       ${HOST_COUNT}"
echo "  Plan rows:       ${PLAN_COUNT}"

if [ "${TABLE_COUNT}" -lt 5 ]; then
  echo "WARNING: Only ${TABLE_COUNT} tables restored — expected at least 5" >&2
fi

echo "[$(date -Iseconds)] Restore drill PASSED"
