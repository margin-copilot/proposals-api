FROM python:3.12-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/
ENV UV_PYTHON_DOWNLOADS=0 UV_LINK_MODE=copy UV_NO_DEV=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-editable
COPY src ./src
RUN uv sync --locked --no-editable

FROM python:3.12-slim
# The SQL driver bundled in mssql-python needs these (Learn: mssql-python installation, Linux).
RUN apt-get update && apt-get install -y --no-install-recommends libltdl7 libkrb5-3 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
USER app
EXPOSE 8000
CMD ["uvicorn", "--factory", "proposals_api.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
