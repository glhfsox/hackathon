# Built from the repository root (see compose.yaml).
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv

WORKDIR /app/backend
ENV UV_LINK_MODE=copy PATH=/app/backend/.venv/bin:$PATH
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend/app ./app
COPY backend/policy.yaml backend/signatures.yaml ./

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
