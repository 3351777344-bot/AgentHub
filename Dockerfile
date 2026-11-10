FROM ghcr.io/astral-sh/uv:0.11.27 AS uv
FROM python:3.13.12-slim

COPY --from=uv /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
RUN groupadd --system agenthub && useradd --system --gid agenthub --home-dir /app agenthub
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --locked --no-dev --no-editable
RUN mkdir -p /app/data/documents && chown -R agenthub:agenthub /app/data
USER agenthub
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "agenthub.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
