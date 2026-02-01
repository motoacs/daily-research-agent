FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install uv (network required at build time).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates nodejs npm \
 && curl -LsSf https://astral.sh/uv/install.sh | sh \
 && apt-get purge -y --auto-remove curl \
 && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:${PATH}"

COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md

# If you add uv.lock later, copy it too for reproducible installs.
# COPY uv.lock /app/uv.lock

RUN uv sync --no-dev

COPY . /app

# Default command is a no-op; execution is driven via `docker compose run --rm ...`.
CMD ["sleep", "infinity"]
