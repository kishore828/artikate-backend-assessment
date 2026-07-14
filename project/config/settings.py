"""
Django settings for the ``config`` project (Artikate Studio backend
assessment).

Design goals
------------
* Boot with **zero external services** — defaults to SQLite so the
  reviewer can `pip install -r requirements.txt && python manage.py
  migrate && python manage.py runserver` in under 5 minutes.
* Allow override via environment variables for PostgreSQL / Redis when
  the reviewer wants to exercise the production-shaped stack.
* Wire up Celery (Section 2) and django-silk (Section 1 profiler) with
  sensible, well-documented defaults.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-override-me-in-production-via-env-var",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = os.environ.get(
    "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1"
).split(",")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "silk",  # Section 1: query / request profiler
    # Local apps (assessment sections)
    "section1_diagnose",
    "section2_queue",
    "section3_tenant",
]

MIDDLEWARE = [
    # TenantMiddleware MUST run before any view-touching middleware so the
    # tenant ContextVar is set before any ORM query fires inside downstream
    # middleware (e.g. AuthenticationMiddleware's session lookups).
    "section3_tenant.middleware.TenantMiddleware",
    "silk.middleware.SilkyMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database
#   Default = SQLite (zero-friction dev / tests).
#   Set DATABASE_URL=postgres://... to use PostgreSQL (which exercises the
#   `psycopg2-binary` dependency required by the brief).
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///db.sqlite3")

if DATABASE_URL.startswith("postgres"):
    # Minimal manual parse — keeps settings.py dependency-free (no dj-database-url).
    # Format: postgres://USER:PASSWORD@HOST:PORT/NAME
    import re
    m = re.match(
        r"postgres://(?P<user>[^:]+):(?P<pwd>[^@]*)@(?P<host>[^:/]+)(?::(?P<port>\d+))?/(?P<name>.+)",
        DATABASE_URL,
    )
    if not m:
        raise RuntimeError(f"Cannot parse DATABASE_URL: {DATABASE_URL}")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": m.group("name"),
            "USER": m.group("user"),
            "PASSWORD": m.group("pwd"),
            "HOST": m.group("host"),
            "PORT": m.group("port") or "5432",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ---------------------------------------------------------------------------
# Auth / i18n
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# django-silk (Section 1 profiler)
# ---------------------------------------------------------------------------
SILK_METADATA = True
SILK_INTERCEPT_PERCENT = 100
SILK_PYTHON_PROFILER = True
# In tests we sometimes disable silk to keep connection.queries clean.
SILK_ENABLE = os.environ.get("SILK_ENABLE", "1") == "1"

# ---------------------------------------------------------------------------
# Celery (Section 2)
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = os.environ.get(
    "CELERY_BROKER_URL", "redis://localhost:6379/0"
)
CELERY_RESULT_BACKEND = os.environ.get(
    "CELERY_RESULT_BACKEND", "redis://localhost:6379/0"
)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"

# --- SIGKILL resilience -----------------------------------------------------
# `acks_late=True` defers the ACK to *after* the task body finishes, so a
# worker killed mid-execution (SIGKILL, OOM, host failure) leaves the
# message un-acked in Redis; RabbitMQ/Redis will redeliver it once the
# connection drops.
CELERY_TASK_ACKS_LATE = True
# `reject_on_worker_lost=True` tells the broker to requeue the message
# (rather than dropping it) when the worker child disappears.
CELERY_TASK_REJECT_ON_WORKER_LOST = True
# Track STARTED state so we can see in-flight tasks in `celery inspect`.
CELERY_TASK_TRACK_STARTED = True

# During tests we flip these to eager so the suite runs without a worker.
CELERY_TASK_ALWAYS_EAGER = os.environ.get(
    "CELERY_TASK_ALWAYS_EAGER", "False"
).lower() in ("1", "true", "yes")
CELERY_TASK_EAGER_PROPAGATES = True

# ---------------------------------------------------------------------------
# Redis (Section 2 rate limiter)
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# ---------------------------------------------------------------------------
# Email (Section 2) — stubbed for tests / dev.
# ---------------------------------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_FROM_EMAIL = "no-reply@artikate.example"
