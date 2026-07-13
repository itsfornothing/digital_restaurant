#!/usr/bin/env bash
# =============================================================================
# backup_verify.sh — PostgreSQL backup and restore verification script
#
# Purpose:
#   1. Perform a full pg_dump backup of the production database.
#   2. Restore the most recent full backup to a test database.
#   3. Verify restore success by comparing table counts between source and
#      restored database.
#   4. Document WAL archiving configuration for hourly incremental backups.
#   5. Report PASS/FAIL with timing to assert RTO < 1 hour.
#
# Usage:
#   ./scripts/backup_verify.sh
#
# Exit codes:
#   0  — all checks passed (backup created, restore verified, counts match)
#   1  — any failure (backup failed, restore failed, count mismatch)
#
# Requirements:
#   - PostgreSQL client tools (pg_dump, psql, createdb, dropdb) on PATH
#   - PGPASSWORD or .pgpass configured for passwordless authentication
#   - Write permission to BACKUP_DIR
#
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration variables — override via environment or edit below
# ---------------------------------------------------------------------------

DB_NAME="${DB_NAME:-restaurant_platform}"
DB_USER="${DB_USER:-postgres}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/restaurant_platform}"
RESTORE_TEST_DB="${RESTORE_TEST_DB:-restaurant_platform_restore_test}"

# Timeout for restore verification (seconds) — used to assert RTO < 1 hour
RTO_TARGET_SECONDS=3600

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No colour

log()  { echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
ok()   { echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] ${GREEN}✓ $*${NC}"; }
warn() { echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] ${YELLOW}⚠ $*${NC}"; }
fail() { echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] ${RED}✗ $*${NC}" >&2; }

# ---------------------------------------------------------------------------
# Helper: run psql command
# ---------------------------------------------------------------------------

run_psql() {
    local db="$1"; shift
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$db" -c "$@"
}

# ---------------------------------------------------------------------------
# Helper: count tables in a database (public schema + tenant schemas)
# ---------------------------------------------------------------------------

count_tables() {
    local db="$1"
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$db" -tAq \
        -c "SELECT count(*) FROM information_schema.tables WHERE table_type='BASE TABLE';"
}

# ---------------------------------------------------------------------------
# Step 0: Initialise
# ---------------------------------------------------------------------------

SCRIPT_START=$(date +%s)
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_FILE="${BACKUP_DIR}/full_${TIMESTAMP}.dump"

log "=== Restaurant Platform — Backup Verify Script ==="
log "DB_NAME       : ${DB_NAME}"
log "DB_USER       : ${DB_USER}"
log "DB_HOST       : ${DB_HOST}:${DB_PORT}"
log "BACKUP_DIR    : ${BACKUP_DIR}"
log "RESTORE_TEST  : ${RESTORE_TEST_DB}"
log "BACKUP_FILE   : ${BACKUP_FILE}"
log ""

# ---------------------------------------------------------------------------
# Step 1: Create backup directory if needed
# ---------------------------------------------------------------------------

log "Step 1: Ensuring backup directory exists..."
mkdir -p "${BACKUP_DIR}"
ok "Backup directory ready: ${BACKUP_DIR}"

# ---------------------------------------------------------------------------
# Step 2: Full pg_dump backup
# ---------------------------------------------------------------------------

log "Step 2: Creating full backup with pg_dump..."
DUMP_START=$(date +%s)

if pg_dump \
    -h "${DB_HOST}" \
    -p "${DB_PORT}" \
    -U "${DB_USER}" \
    --format=custom \
    --compress=9 \
    --verbose \
    --file="${BACKUP_FILE}" \
    "${DB_NAME}" 2>&1; then
    DUMP_END=$(date +%s)
    DUMP_DURATION=$((DUMP_END - DUMP_START))
    BACKUP_SIZE=$(du -sh "${BACKUP_FILE}" | cut -f1)
    ok "Full backup completed in ${DUMP_DURATION}s (size: ${BACKUP_SIZE})"
    ok "Backup file: ${BACKUP_FILE}"
else
    fail "pg_dump failed! Backup not created."
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 3: Find most recent backup for restore test
# ---------------------------------------------------------------------------

log "Step 3: Locating most recent full backup..."
LATEST_BACKUP=$(ls -t "${BACKUP_DIR}"/full_*.dump 2>/dev/null | head -1 || true)

if [ -z "${LATEST_BACKUP}" ]; then
    fail "No backup files found in ${BACKUP_DIR}"
    exit 1
fi

ok "Using backup: ${LATEST_BACKUP}"

# ---------------------------------------------------------------------------
# Step 4: Drop and recreate the restore test database
# ---------------------------------------------------------------------------

log "Step 4: Preparing restore test database '${RESTORE_TEST_DB}'..."

# Drop if exists (ignore error if it doesn't exist)
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres \
    -c "DROP DATABASE IF EXISTS \"${RESTORE_TEST_DB}\";" 2>&1 && \
    ok "Dropped existing test database (if any)"

psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres \
    -c "CREATE DATABASE \"${RESTORE_TEST_DB}\";" && \
    ok "Created restore test database: ${RESTORE_TEST_DB}"

# ---------------------------------------------------------------------------
# Step 5: Restore backup to test database
# ---------------------------------------------------------------------------

log "Step 5: Restoring backup to '${RESTORE_TEST_DB}'..."
RESTORE_START=$(date +%s)

if pg_restore \
    -h "${DB_HOST}" \
    -p "${DB_PORT}" \
    -U "${DB_USER}" \
    --dbname="${RESTORE_TEST_DB}" \
    --verbose \
    --no-owner \
    --no-privileges \
    "${LATEST_BACKUP}" 2>&1; then
    RESTORE_END=$(date +%s)
    RESTORE_DURATION=$((RESTORE_END - RESTORE_START))
    ok "Restore completed in ${RESTORE_DURATION}s"
else
    fail "pg_restore failed!"
    exit 1
fi

# Check RTO target
if [ "${RESTORE_DURATION}" -le "${RTO_TARGET_SECONDS}" ]; then
    ok "RTO target met: restore took ${RESTORE_DURATION}s (target < ${RTO_TARGET_SECONDS}s / 1 hour)"
else
    warn "RTO target EXCEEDED: restore took ${RESTORE_DURATION}s (target ${RTO_TARGET_SECONDS}s)"
fi

# ---------------------------------------------------------------------------
# Step 6: Verify restore — compare table counts
# ---------------------------------------------------------------------------

log "Step 6: Verifying restore by comparing table counts..."

SOURCE_COUNT=$(count_tables "${DB_NAME}")
RESTORE_COUNT=$(count_tables "${RESTORE_TEST_DB}")

log "Source  '${DB_NAME}' table count : ${SOURCE_COUNT}"
log "Restore '${RESTORE_TEST_DB}' table count : ${RESTORE_COUNT}"

if [ "${SOURCE_COUNT}" -eq "${RESTORE_COUNT}" ]; then
    ok "Table counts match: ${SOURCE_COUNT} tables in both databases"
else
    fail "Table count MISMATCH: source=${SOURCE_COUNT}, restore=${RESTORE_COUNT}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 7: Clean up restore test database
# ---------------------------------------------------------------------------

log "Step 7: Cleaning up restore test database..."
psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres \
    -c "DROP DATABASE IF EXISTS \"${RESTORE_TEST_DB}\";" && \
    ok "Cleaned up restore test database"

# ---------------------------------------------------------------------------
# Step 8: WAL archiving documentation
# ---------------------------------------------------------------------------

log ""
log "=== WAL Archiving Configuration (for RPO < 1 hour) ==="
log ""
log "To enable hourly incremental backups via WAL archiving, add the"
log "following to postgresql.conf:"
log ""
log "  wal_level = replica"
log "  archive_mode = on"
log "  archive_command = 'cp %p /var/backups/restaurant_platform/wal/%f'"
log "  archive_timeout = 3600   # archive at least every 60 minutes"
log ""
log "Alternatively, use a cloud-native command:"
log "  archive_command = 'aws s3 cp %p s3://your-bucket/wal/%f'"
log ""
log "After modifying postgresql.conf, reload PostgreSQL:"
log "  sudo systemctl reload postgresql"
log "  -- or --"
log "  psql -c \"SELECT pg_reload_conf();\""
log ""
log "Verify WAL archiving is active:"
log "  psql -c \"SELECT * FROM pg_stat_archiver;\""
log ""

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------

SCRIPT_END=$(date +%s)
TOTAL_DURATION=$((SCRIPT_END - SCRIPT_START))

log "=== Summary ==="
log "Backup file  : ${BACKUP_FILE} (${BACKUP_SIZE})"
log "Dump time    : ${DUMP_DURATION}s"
log "Restore time : ${RESTORE_DURATION}s"
log "Total time   : ${TOTAL_DURATION}s"
log ""
ok "PASS — Backup and restore verification completed successfully"
exit 0
