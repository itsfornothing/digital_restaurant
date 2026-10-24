#!/bin/sh
# docker-entrypoint.sh — runs migrations before starting the application process.
# Called by the Dockerfile ENTRYPOINT.

set -e

# Wait for database connection
if [ -n "$DATABASE_URL" ]; then
    echo "[entrypoint] Waiting for PostgreSQL via DATABASE_URL…"
    until pg_isready -d "$DATABASE_URL" -t 5 2>/dev/null; do
        echo "[entrypoint] Waiting for database…"
        sleep 2
    done
else
    echo "[entrypoint] Waiting for PostgreSQL at ${DB_HOST:-db}:${DB_PORT:-5432}…"
    until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-postgres}" -t 5 2>/dev/null; do
        echo "[entrypoint] Waiting for database…"
        sleep 2
    done
fi
echo "[entrypoint] PostgreSQL is ready."

echo "[entrypoint] Running shared-schema migrations…"
python manage.py migrate_schemas --shared --noinput -v 3 2>&1 | tail -50

echo "[entrypoint] Running tenant schema migrations…"
python manage.py migrate_schemas --noinput -v 3 2>&1 | tail -50

echo "[entrypoint] Bootstrapping first tenant (if needed)…"
python manage.py bootstrap -v 2 2>&1 || echo "[entrypoint] Bootstrap skipped."

echo "[entrypoint] Collecting static files…"
python manage.py collectstatic --noinput 2>&1 | tail -20 || echo "[entrypoint] Static collection completed with warnings."

# Replace port arg with $PORT if set (Render sets this automatically)
ARGS="$*"
if [ -n "$PORT" ]; then
    ARGS=$(echo "$ARGS" | sed "s/-p [0-9]*/-p $PORT/")
fi

echo "[entrypoint] Starting: $ARGS"
exec sh -c "$ARGS"
