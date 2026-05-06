# Multi-stage build: install deps in builder, copy lean runtime image
# Smaller final image = faster deploy + smaller egress on cold start.

FROM python:3.12-slim AS builder

# Install uv binary directly (faster than pip install uv)
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /uvx /bin/

WORKDIR /app

# Copy dependency manifests first for layer cache efficiency
COPY pyproject.toml uv.lock README.md ./

# Install deps into a project venv (uv creates .venv/ in WORKDIR)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source last so code edits don't bust the dep cache
COPY app ./app
COPY frontend ./frontend
COPY data ./data

# Now install the project itself (cheap step since deps already cached)
RUN uv sync --frozen --no-dev

# ─── Final runtime image ──────────────────────────────────────

FROM python:3.12-slim

# Non-root user for least privilege
RUN useradd --create-home --shell /bin/bash app

WORKDIR /app

# Copy the prebuilt venv + source from builder
COPY --from=builder --chown=app:app /app /app

# Activate the venv by putting it on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER app

# Railway injects $PORT at runtime, default 8000 for local docker
EXPOSE 8000

# uvicorn directly — single-worker is fine for free/hobby tier,
# in-memory metrics collector + structlog request-id rely on single proc
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]