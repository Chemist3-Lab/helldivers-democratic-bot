# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1

# Install system dependencies (ca-certificates, curl, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv from the official binary installer
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Set working directory
WORKDIR /app

# Ensure persistent data directory volume exists
RUN mkdir -p /app/data

# Copy pyproject.toml and dependencies specification first for layer caching
COPY pyproject.toml .

# Install dependencies using uv
RUN uv pip install --no-cache -r pyproject.toml

# Copy source code and other files
COPY src/ ./src/
COPY ARCHITECTURE.md .
COPY README.md* .

# Default entrypoint runs the bot module
CMD ["python", "-m", "src.main"]
