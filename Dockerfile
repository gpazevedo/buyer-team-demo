FROM python:3.14-slim

RUN pip install uv

WORKDIR /app

COPY pyproject.toml .
RUN uv sync --no-dev

COPY src/ src/
RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

EXPOSE 8000

CMD ["uvicorn", "test_tenant_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
