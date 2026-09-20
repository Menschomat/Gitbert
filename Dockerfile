# ==========================================
# Stage 1: Build virtual environment with uv
# ==========================================
FROM python:3.14-slim-bookworm AS builder

# Copy uv binary from official Astral image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Cache dependencies and sync without dev dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy application files and finalize package installation
COPY pyproject.toml README.md uv.lock ./
COPY gitbert/ ./gitbert/
COPY main.py ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ==========================================
# Stage 2: Minimal runtime image
# ==========================================
FROM python:3.14-slim-bookworm AS runner

WORKDIR /app

# Create unprivileged system user
RUN groupadd -g 1000 appgroup && \
    useradd -u 1000 -g appgroup -s /bin/sh -m appuser && \
    mkdir -p /app/.adk && \
    chown -R appuser:appgroup /app

# Copy virtual environment and project files from builder
COPY --from=builder --chown=appuser:appgroup /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appgroup /app/gitbert /app/gitbert
COPY --from=builder --chown=appuser:appgroup /app/main.py /app/main.py
COPY --from=builder --chown=appuser:appgroup /app/pyproject.toml /app/pyproject.toml

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOST=0.0.0.0

USER appuser

EXPOSE 8080

# Default command: launch the ADK API server with Web UI
CMD ["adk", "api_server", ".", "--host", "0.0.0.0", "--port", "8080", "--with_ui"]
