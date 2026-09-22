# Note2TimeSheet v2 - runtime image
# Build & run with:  docker compose up -d --build   (see docker-compose.yml)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_PORT=5600 \
    APP_HOST=0.0.0.0 \
    AUTO_OPEN_BROWSER=false \
    TIMESHEET_DATA_DIR=/app/data \
    TZ=Europe/Rome

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
# pywin32 is skipped automatically on Linux (environment marker in requirements.txt).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code (.env, data/, tests/, .venv are excluded via .dockerignore).
COPY . .

# Persistent data lives here (mounted as a volume by docker-compose.yml).
RUN mkdir -p /app/data

EXPOSE 5600

# Liveness probe: /api/config answers 200 when Flask is up.
# The port is read from APP_PORT so overriding it in compose keeps the check valid.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, sys, urllib.request; url = 'http://localhost:%s/api/config' % os.environ.get('APP_PORT', '5600'); sys.exit(0 if urllib.request.urlopen(url, timeout=4).status == 200 else 1)" || exit 1

CMD ["python", "app.py"]
