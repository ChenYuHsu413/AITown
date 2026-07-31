# AI Town -- single long-lived container (FastAPI + WebSocket + background
# asyncio simulation loop). Built for always-on container platforms
# (Railway / Fly.io), NOT serverless: the process must stay up so the sim
# keeps running. Idle auto-suspend (no clients for 10s) keeps free tiers cheap.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# --- dependency layer -------------------------------------------------
# Copy ONLY requirements first so this layer is cached and pip re-runs
# only when requirements.txt actually changes (not on every code edit).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- non-root user ----------------------------------------------------
RUN useradd --create-home --uid 10001 appuser

# --- application code -------------------------------------------------
# .dockerignore keeps .git/.env/__pycache__/node_modules out of the image.
COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

# Shell form (NOT exec form) so ${PORT}, injected by Railway/Fly at runtime,
# is expanded by /bin/sh. Falls back to 8000 for local `docker run`.
CMD uvicorn backend.app.server:app --host 0.0.0.0 --port ${PORT:-8000}
