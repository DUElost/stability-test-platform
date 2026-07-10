#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# pg_backup.sh — PostgreSQL logical backup with rotation
#
# Usage:
#   ./pg_backup.sh                          # default: localhost:5432/stability
#   PGHOST=db PGUSER=stp ./pg_backup.sh  # override via env
#
# Cron example (daily at 02:30):
#   30 2 * * * /home/debian13/stability-test-platform/scripts/pg_backup.sh >> /home/debian13/stability-test-platform/logs/pg_backup.log 2>&1
#
# Environment variables (all optional, sensible defaults for in-subnet deploy):
#   PGHOST       — default 127.0.0.1
#   PGPORT       — default 5432
#   PGUSER       — default stp
#   PGDATABASE   — default stp
#   BACKUP_DIR   — default /home/debian13/stability-test-platform/backups
#   KEEP_DAYS    — default 7 (delete backups older than N days)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-stp}"
PGDATABASE="${PGDATABASE:-stp}"
BACKUP_DIR="${BACKUP_DIR:-/home/debian13/stability-test-platform/backups}"
KEEP_DAYS="${KEEP_DAYS:-7}"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="${PGDATABASE}_${TIMESTAMP}.sql.gz"
FILEPATH="${BACKUP_DIR}/${FILENAME}"

mkdir -p "${BACKUP_DIR}"

echo "[$(date -Iseconds)] Starting backup: ${PGUSER}@${PGHOST}:${PGPORT}/${PGDATABASE}"

if ! pg_dump \
    -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" \
    --no-owner --no-privileges \
    --format=plain \
    "${PGDATABASE}" 2>/dev/null | gzip > "${FILEPATH}"; then
  echo "[$(date -Iseconds)] ERROR: pg_dump failed" >&2
  rm -f "${FILEPATH}"
  exit 1
fi

SIZE=$(du -h "${FILEPATH}" | cut -f1)
echo "[$(date -Iseconds)] Backup completed: ${FILENAME} (${SIZE})"

# ── Rotation ──
DELETED=$(find "${BACKUP_DIR}" -name "${PGDATABASE}_*.sql.gz" -mtime +"${KEEP_DAYS}" -print -delete 2>/dev/null | wc -l)
if [ "${DELETED}" -gt 0 ]; then
  echo "[$(date -Iseconds)] Rotated ${DELETED} backup(s) older than ${KEEP_DAYS} days"
fi

echo "[$(date -Iseconds)] Remaining backups: $(ls -1 "${BACKUP_DIR}/${PGDATABASE}"_*.sql.gz 2>/dev/null | wc -l)"
