FROM python:3.14-slim AS build

# uv as the installer (project standard), copied as a static binary — arch-correct.
COPY --from=ghcr.io/astral-sh/uv:0.9.3 /uv /uvx /bin/

WORKDIR /app

# Resolve dependencies first so this layer caches on pyproject changes only.
COPY pyproject.toml .
RUN uv sync --no-dev --no-install-project

# Install the project itself once source is present.
COPY src/ src/
RUN uv sync --no-dev

FROM python:3.14-slim

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src src/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

EXPOSE 8000

CMD ["uvicorn", "test_tenant_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
