# Agentic Trading OS - Production Dockerfile
# Multi-stage build for minimal image size

# ============================================
# Stage 1: Builder
# ============================================
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY Alpha\ IO/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir numpy scipy && \
    pip install --no-cache-dir websocket-client cryptography requests aiohttp && \
    pip install --no-cache-dir -r requirements.txt || true

# ============================================
# Stage 2: Production
# ============================================
FROM python:3.11-slim as production

# Labels
LABEL maintainer="Agentic Trading OS"
LABEL version="1.0.0"
LABEL description="Production-ready algorithmic trading system"

# Security: Run as non-root user
RUN groupadd -r trading && useradd -r -g trading trading

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code.
# ATOS-P2-DEPLOY-001: `COPY config/ ./config/` used to be here. There is no
# config/ at the repository root, so the build failed outright; and had one
# existed it would have baked whatever it held into the image. Runtime
# configuration and credentials are mounted or injected, never copied. See
# .dockerignore for the paths excluded from the COPY below.
COPY Alpha\ IO/ ./Alpha\ IO/
COPY setup.sh ./

# Create directories with proper permissions
RUN mkdir -p logs data .credentials && \
    chown -R trading:trading /app

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TRADING_MODE=paper
ENV LOG_LEVEL=INFO

# Switch to non-root user
USER trading

# Expose API port
EXPOSE 8080

# Liveness only - ATOS-P2-DEPLOY-001.
#
# Docker's HEALTHCHECK drives restarts, so it must be wired to liveness and
# never to readiness. A trading system that is unsafe to trade is usually
# worse off restarted: the restart discards what it had established about the
# broker's book and re-opens the question of what is actually held.
# Readiness is a separate, non-restarting probe at /api/v1/ready, which
# returns 503 while the system may not add new risk.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/v1/health')" || exit 1

# Default command
CMD ["python", "Alpha IO/trading_system.py", "--mode", "paper"]

# ============================================
# Stage 3: Development
# ============================================
FROM production as development

USER root

# Install development tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    vim \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install dev dependencies
RUN pip install --no-cache-dir \
    pytest \
    pytest-asyncio \
    pytest-cov \
    black \
    flake8 \
    mypy

USER trading

# Override command for development
CMD ["python", "Alpha IO/trading_system.py", "--mode", "all-tests"]
