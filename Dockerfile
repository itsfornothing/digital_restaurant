FROM python:3.12-slim-bookworm

# Prevent .pyc files and enable unbuffered stdout/stderr for Docker logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runtime libs:
#   libpq5         → psycopg2-binary (pre-built wheel, just needs the runtime lib)
#   libffi8        → argon2-cffi (pre-built wheel)
#   libcairo2 + pango + gdk-pixbuf → WeasyPrint PDF rendering
#   curl           → health checks in docker-compose
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        postgresql-client \
        libffi8 \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf2.0-0 \
        shared-mime-info \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user for running the application
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Install Python dependencies first (layer cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Collect static files (requires DJANGO_SETTINGS_MODULE + DB to not be needed at build time)
RUN DJANGO_SETTINGS_MODULE=config.settings.production \
    DJANGO_SECRET_KEY=build-time-placeholder \
    DB_NAME=placeholder DB_USER=placeholder DB_PASSWORD=placeholder \
    DB_HOST=placeholder DB_PORT=5432 \
    R2_ENDPOINT_URL=placeholder R2_ACCESS_KEY_ID=placeholder \
    R2_SECRET_ACCESS_KEY=placeholder R2_BUCKET_NAME=placeholder \
    python manage.py collectstatic --noinput 2>/dev/null || true

# Transfer ownership to non-root user
RUN chown -R appuser:appgroup /app
USER appuser

# Entrypoint: run Daphne (ASGI, HTTP + WebSocket) by default
# Single-process required for InMemoryChannelLayer.
EXPOSE 8000

ENTRYPOINT ["sh", "docker-entrypoint.sh"]
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
