#!/bin/sh
# docker-entrypoint.sh — runs migrations before starting the application process.
# Called by the Dockerfile ENTRYPOINT.

set -e

# Parse DATABASE_URL into DB_* vars when individual vars are not set
if [ -n "$DATABASE_URL" ] && [ -z "$DB_HOST" ]; then
    DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|.*//.*@\([^:/]*\).*|\1|p')
    DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
    [ -z "$DB_PORT" ] && DB_PORT=5432
fi

echo "[entrypoint] Waiting for PostgreSQL at ${DB_HOST:-db}:${DB_PORT:-5432}…"
until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-postgres}" -t 5 2>/dev/null; do
    echo "[entrypoint] Waiting for database…"
    sleep 2
done
echo "[entrypoint] PostgreSQL is ready."

echo "[entrypoint] Running shared-schema migrations…"
python manage.py migrate_schemas --shared --noinput -v 3 2>&1 | tail -50

echo "[entrypoint] Running tenant schema migrations…"
python manage.py migrate_schemas --noinput -v 3 2>&1 | tail -50

echo "[entrypoint] Bootstrapping first tenant (if needed)…"
python manage.py bootstrap -v 2 2>&1 || echo "[entrypoint] Bootstrap skipped."

# Replace port arg with $PORT if set (Render sets this automatically)
ARGS="$*"
if [ -n "$PORT" ]; then
    ARGS=$(echo "$ARGS" | sed "s/-p [0-9]*/-p $PORT/")
fi

echo "[entrypoint] Starting: $ARGS"
exec sh -c "$ARGS"
