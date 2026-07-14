# Multi-stage build for the Artikate backend.
#
# Stage 1: build wheels for all deps in a slim image.
# Stage 2: copy only the installed wheels + app code into a final image.

# ---------- Stage 1: builder ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Install build deps for psycopg2-binary (libpq-dev) and gcc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings_prod

# Runtime deps: libpq5 for psycopg2, no build tools.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 app

WORKDIR /app

# Copy installed packages from builder.
COPY --from=builder /install /usr/local

# Copy application code.
COPY . /app/

RUN chown -R app:app /app
USER app

EXPOSE 8000

# Default command — overridden in docker-compose for the worker.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "30"]
