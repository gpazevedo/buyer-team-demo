# Build from the repo root: docker build -f demo-harness-project/Dockerfile .
# demo-harness-project/backend depends on test_tenant_app/backend as a uv
# workspace package, so the build context must include both directories.

# ---- frontend ----
FROM node:24-slim AS frontend-build
WORKDIR /app
RUN corepack enable

COPY demo-harness-project/frontend/package.json demo-harness-project/frontend/pnpm-lock.yaml demo-harness-project/frontend/pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    corepack prepare --activate && pnpm install --frozen-lockfile

COPY demo-harness-project/frontend/ ./
RUN pnpm build

# ---- python deps ----
FROM python:3.14-slim AS python-build

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/
WORKDIR /app

# Resolve dependencies first so this layer caches on pyproject/lock changes only.
COPY pyproject.toml uv.lock ./
COPY test_tenant_app/backend/pyproject.toml test_tenant_app/backend/pyproject.toml
COPY demo-harness-project/backend/pyproject.toml demo-harness-project/backend/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace

# Install the workspace packages themselves once source is present.
COPY test_tenant_app/backend/src test_tenant_app/backend/src
COPY demo-harness-project/backend/src demo-harness-project/backend/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package demo-harness

# ---- runtime ----
FROM python:3.14-slim

WORKDIR /app
COPY --from=python-build /app/.venv .venv
COPY --from=python-build /app/test_tenant_app test_tenant_app
COPY --from=python-build /app/demo-harness-project demo-harness-project
COPY --from=frontend-build /app/dist demo-harness-project/frontend/dist

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/demo-harness-project/backend/src:/app/test_tenant_app/backend/src"
ENV FRONTEND_DIST="/app/demo-harness-project/frontend/dist"

WORKDIR /app/demo-harness-project/backend
EXPOSE 8000

CMD ["uvicorn", "demo_harness.main:app", "--host", "0.0.0.0", "--port", "8000"]
