FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:${PATH}"

COPY pyproject.toml uv.lock README.md ./
# Make dependency download resilient on slow/flaky networks: raise uv's per-request
# HTTP timeout (default 30s) and cap concurrent connections so PyPI does not drop
# them under load (avoids "peer closed connection" TLS resets, especially when all
# services build in parallel).
ENV UV_HTTP_TIMEOUT=120
ENV UV_CONCURRENT_DOWNLOADS=4
RUN uv sync --frozen --no-install-project

COPY app/ ./

EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]