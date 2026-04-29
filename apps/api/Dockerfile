FROM ghcr.io/astral-sh/uv:0.7.21 AS uv

FROM python:3.12-slim AS runtime

ARG AQ_VERSION=0.0.0-dev
ARG AQ_GIT_COMMIT=0000000
ARG AQ_BUILT_AT=1970-01-01T00:00:00+00:00

ENV AQ_VERSION=${AQ_VERSION} \
    AQ_GIT_COMMIT=${AQ_GIT_COMMIT} \
    AQ_BUILT_AT=${AQ_BUILT_AT} \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=uv /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
COPY apps/api ./apps/api
COPY apps/cli ./apps/cli
COPY tests ./tests
COPY scripts ./scripts

RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["sh", "-c", "echo 'Running alembic upgrade head' && uv run --frozen --no-dev alembic -c apps/api/alembic.ini upgrade head && exec uv run --frozen --no-dev uvicorn aq_api.app:app --host 0.0.0.0 --port 8000"]
