# =========================================================
# Smart Cushion Fog Node – Dockerfile
#
# Build:   docker build -t smart-cushion-fog .
# Run:     docker run --env-file .env smart-cushion-fog
# =========================================================

FROM python:3.11-slim

# ── Metadata ───────────────────────────────────────────────────────────────
LABEL maintainer="Smart Cushion Team"
LABEL description="Smart Cushion Fog Node – posture classification & real-time relay"

# ── System dependencies ────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ──────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies (cached layer) ────────────────────────────────────
# Copy only requirements first to leverage Docker layer caching.
# Reinstall only when requirements.txt changes, not on every code change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ─────────────────────────────────────────────────────
COPY . .

# ── Security: run as non-root user ────────────────────────────────────────
RUN adduser --disabled-password --gecos '' --uid 1001 foguser \
    && mkdir -p /app/logs /app/ai/models \
    && chown -R foguser:foguser /app
USER foguser

# ── Port: WebSocket server ─────────────────────────────────────────────────
EXPOSE 8765

# ── Health check ───────────────────────────────────────────────────────────
# A simple check that the Python process is alive.
# Upgrade to an actual HTTP health endpoint in production.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import asyncio, websockets; asyncio.run(websockets.connect('ws://localhost:8765'))" || exit 1

# ── Entry point ────────────────────────────────────────────────────────────
CMD ["python", "app.py"]
