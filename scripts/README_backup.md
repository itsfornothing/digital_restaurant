# Database Backup & Restore Procedures

## Restaurant Management & Smart Ordering Platform

---

## Overview

This document describes the backup and restore strategy for the PostgreSQL 15 database used by the Restaurant Management & Smart Ordering Platform.

**Targets:**
| Metric | Target | Method |
|--------|--------|--------|
| RTO (Recovery Time Objective) | < 1 hour | Restore from most recent full backup + apply WAL segments |
| RPO (Recovery Point Objective) | < 1 hour | WAL archiving every 60 minutes via `archive_timeout` |

---

## Backup Strategy

### Daily Full Backup (pg_dump)

A full logical backup is performed once per day using `pg_dump` in custom (compressed) format.

**What is backed up:** all schemas (`public` + all `tenant_{slug}` schemas), including tables, indexes, constraints, sequences, roles, and data.

**Backup format:** PostgreSQL custom format (`--format=custom`) with compression level 9.

**Storage location:** `/var/backups/restaurant_platform/full_<YYYYMMDD_HHMMSS>.dump`

**Retention policy:** keep the last 7 daily backups; archive older backups to cold storage (Cloudflare R2 or equivalent).

### Hourly Incremental Backup (WAL Archiving)

Write-Ahead Log (WAL) segments are archived continuously with `archive_command`, with `archive_timeout = 3600` ensuring a WAL segment is archived at least every 60 minutes even during idle periods.

**RPO achieved:** in the worst case, up to 1 hour of data loss (the last un-archived WAL segment).

---

## Configuring WAL Archiving in `postgresql.conf`

Add or modify the following settings in `/etc/postgresql/15/main/postgresql.conf` (adjust path for your OS/installation):

```
# Enable WAL archiving
wal_level = replica
archive_mode = on

# Archive command — writes WAL files to local directory
# Replace with cloud command for production (see below)
archive_command = 'cp %p /var/backups/restaurant_platform/wal/%f'

# Force archiving at least every 60 minutes, even during idle periods
# This is what ensures RPO < 1 hour during low-traffic windows
archive_timeout = 3600
```

For production, replace the local `cp` command with a cloud-native archive:

```
# AWS S3
archive_command = 'aws s3 cp %p s3://your-bucket/pg-wal/%f'

# Cloudflare R2 (S3-compatible)
archive_command = 'aws s3 cp %p s3://your-r2-bucket/pg-wal/%f --endpoint-url https://<account>.r2.cloudflarestorage.com'
```

After modifying `postgresql.conf`, reload PostgreSQL without a restart:

```bash
# systemd
sudo systemctl reload postgresql

# psql
psql -U postgres -c "SELECT pg_reload_conf();"
```

Verify WAL archiving is active:

```sql
SELECT * FROM pg_stat_archiver;
-- Look for: last_archived_wal, last_archived_time, failed_count (should be 0)
```

---

## Running a Full Backup (Manual)

Run the `backup_verify.sh` script from the project root:

```bash
# With default configuration
./scripts/backup_verify.sh

# With environment variable overrides
DB_NAME=restaurant_platform \
DB_USER=postgres \
DB_HOST=db.example.com \
BACKUP_DIR=/mnt/backups \
./scripts/backup_verify.sh
```

The script:
1. Creates a `pg_dump` backup to `$BACKUP_DIR/full_<timestamp>.dump`
2. Restores it to a temporary test database
3. Compares table counts between source and restored DB
4. Reports PASS/FAIL and timing
5. Cleans up the test database

Exit code `0` = success, `1` = failure.

---

## Running a Test Restore (Manual)

To test a restore from a specific backup file without running the full script:

```bash
# 1. Create a test restore database
createdb -U postgres restaurant_restore_test

# 2. Restore from the backup
pg_restore \
  -U postgres \
  --dbname=restaurant_restore_test \
  --no-owner \
  --no-privileges \
  /var/backups/restaurant_platform/full_20260101_020000.dump

# 3. Verify by comparing table counts
psql -U postgres -d restaurant_platform \
  -c "SELECT count(*) FROM information_schema.tables WHERE table_type='BASE TABLE';"

psql -U postgres -d restaurant_restore_test \
  -c "SELECT count(*) FROM information_schema.tables WHERE table_type='BASE TABLE';"

# 4. Clean up
dropdb -U postgres restaurant_restore_test
```

---

## Point-in-Time Recovery (PITR) with WAL

To recover to a specific point in time (e.g., to a timestamp just before data corruption):

```bash
# 1. Stop PostgreSQL
sudo systemctl stop postgresql

# 2. Restore the last full backup
pg_restore \
  -U postgres \
  --dbname=restaurant_platform \
  --no-owner \
  /var/backups/restaurant_platform/full_<latest>.dump

# 3. Create recovery.conf (PostgreSQL < 12) or postgresql.auto.conf entry:
cat >> /var/lib/postgresql/15/main/postgresql.auto.conf << 'EOF'
restore_command = 'cp /var/backups/restaurant_platform/wal/%f %p'
recovery_target_time = '2026-01-15 10:30:00'
recovery_target_action = 'promote'
EOF

# Also create the recovery signal file:
touch /var/lib/postgresql/15/main/recovery.signal

# 4. Start PostgreSQL — it will replay WAL segments up to the target time
sudo systemctl start postgresql

# 5. Monitor recovery progress
tail -f /var/log/postgresql/postgresql-15-main.log
```

---

## RTO and RPO Details

### RTO < 1 Hour

The Recovery Time Objective is the time to restore service after a failure.

Steps and estimated times:
| Step | Estimated Time |
|------|---------------|
| Detect failure and alert | 5 min |
| Provision replacement DB server | 10 min |
| Download latest full backup | 10–15 min |
| Run `pg_restore` | 10–20 min |
| Apply WAL segments (up to 1 hour worth) | 5–10 min |
| Restart application and verify | 5 min |
| **Total** | **~45–65 min** |

For very large databases (>50 GB), scale up the DB instance before restoring to reduce restore time.

### RPO < 1 Hour

The Recovery Point Objective is the maximum data loss acceptable.

- `archive_timeout = 3600` ensures WAL is shipped every 60 minutes.
- In practice, WAL is shipped more frequently during active periods (each time a WAL segment fills — typically every few minutes under normal load).
- Worst case: 1 hour of transactions lost (idle system, WAL shipped exactly every 60 minutes).

---

## Verifying Backups are Running (Cron Job)

Add the following cron job to run backups nightly at 02:00 UTC:

```cron
# /etc/cron.d/restaurant-platform-backup
# Daily full backup at 02:00 UTC
0 2 * * * postgres DB_NAME=restaurant_platform DB_USER=postgres BACKUP_DIR=/var/backups/restaurant_platform /path/to/restaurant_platform/scripts/backup_verify.sh >> /var/log/restaurant-backup.log 2>&1
```

Verify the cron job ran successfully:

```bash
# Check the log file
tail -50 /var/log/restaurant-backup.log

# Verify recent backup files exist
ls -lh /var/backups/restaurant_platform/full_*.dump | tail -7

# Check WAL archiver status
psql -U postgres -c "SELECT last_archived_wal, last_archived_time, failed_count FROM pg_stat_archiver;"
```

Set up an alerting check: if no new `full_*.dump` file has been created in the last 26 hours, trigger a PagerDuty/Alertmanager alert.

---

## Disaster Recovery Runbook

### Scenario: Complete Database Server Loss

**Step 1 — Assess and alert (0–5 min)**
- Confirm PostgreSQL is unreachable (health check endpoint `/health` returning `down`).
- Trigger incident response; notify on-call engineer.

**Step 2 — Provision replacement server (5–15 min)**
- Launch a new PostgreSQL 15 server (cloud VM or managed service).
- Install PostgreSQL 15 and configure `pg_hba.conf` for application access.
- Set `PGPASSWORD` or configure `.pgpass` for the `postgres` user.

**Step 3 — Download latest backup (15–25 min)**
```bash
# From cloud storage
aws s3 cp s3://your-bucket/full_<latest>.dump /var/backups/full_restore.dump

# Verify integrity
pg_restore --list /var/backups/full_restore.dump | wc -l
```

**Step 4 — Create database and restore (25–45 min)**
```bash
createdb -U postgres restaurant_platform
pg_restore -U postgres --dbname=restaurant_platform --no-owner /var/backups/full_restore.dump
```

**Step 5 — Apply WAL segments for PITR (45–55 min)**
```bash
# Download WAL segments from archive covering the period since the last full backup
aws s3 sync s3://your-bucket/pg-wal/ /var/backups/wal/
# Configure recovery (see PITR section above)
```

**Step 6 — Update DNS / connection strings (55–60 min)**
- Update `DB_HOST` environment variable on all application servers.
- Restart Gunicorn, Daphne (ASGI), and Celery workers.
- Run Django system check: `python manage.py check`.

**Step 7 — Smoke test (60+ min)**
- Verify `/health` endpoint returns `{"status": "ok"}`.
- Place a test order through the customer UI.
- Confirm KDS WebSocket notifications are working.
- Confirm financial dashboard loads correctly.

**Step 8 — Post-incident**
- Document the incident timeline.
- Update backup procedures if any gap was found.
- Test restore again within 48 hours to confirm the recovery worked correctly.

---

## Backup Retention Policy

| Backup Type | Retention | Storage |
|-------------|-----------|---------|
| Daily full (`pg_dump`) | 7 days local | `/var/backups/restaurant_platform/` |
| Daily full (archived) | 90 days | Cloudflare R2 / S3 |
| WAL segments | 7 days | Cloudflare R2 / S3 |
| Monthly snapshot | 12 months | Cloudflare R2 / S3 |

Automate retention enforcement:

```bash
# Delete local full backups older than 7 days
find /var/backups/restaurant_platform/full_*.dump -mtime +7 -delete

# Delete WAL segments older than 7 days from archive
# (adjust for cloud storage as needed)
find /var/backups/restaurant_platform/wal/ -mtime +7 -delete
```

---

## Contacts

| Role | Responsibility |
|------|---------------|
| DBA / Platform Engineer | Backup configuration, WAL archiving setup |
| DevOps On-Call | Incident response, server provisioning |
| Tenant Owner / Platform Admin | Business decision on acceptable data loss |
