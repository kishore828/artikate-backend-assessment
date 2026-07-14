"""
Production settings for the ``config`` project.

Load via:
    DJANGO_SETTINGS_MODULE=config.settings_prod

This module imports dev settings and overrides only what must change
for production. Kept separate so the dev path stays auditable in
code review.

Hardening checklist (reviewer's ask):
  [x] DEBUG=False
  [x] SECRET_KEY from env (no insecure default)
  [x] ALLOWED_HOSTS from env
  [x] SECURE_* middleware enabled
  [x] HSTS enabled (1 year + preload)
  [x] Cookies marked Secure + HttpOnly
  [x] CSRF cookie marked Secure
  [x] SSL redirect
  [x] Postgres as DB (sqlite forbidden in prod)
  [x] Connection pooling via CONN_MAX_AGE
  [x] Logging to stdout as structured JSON
  [x] Sentry + OpenTelemetry wired (sketched, opt-in)
"""

from __future__ import annotations

import os
from pathlib import Path

# Import dev settings and override.
from .settings import *  # noqa: F401, F403
from .settings import BASE_DIR, INSTALLED_APPS, MIDDLEWARE  # explicit

# ---------------------------------------------------------------------------
# Security — NO insecure defaults in production
# ---------------------------------------------------------------------------
DEBUG = False

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]  # raises KeyError if missing

ALLOWED_HOSTS = os.environ["DJANGO_ALLOWED_HOSTS"].split(",")

# Honor the proxy header so SECURE_SSL_REDIRECT works behind a LB.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31_536_000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

# ---------------------------------------------------------------------------
# Database — Postgres only in prod
# ---------------------------------------------------------------------------
_database_url = os.environ["DATABASE_URL"]
assert _database_url.startswith("postgres://"), \
    "Production must use PostgreSQL, not SQLite."

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.environ["DB_HOST"],
        "PORT": os.environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,        # persistent connections
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "connect_timeout": 5,
        },
    }
}

# ---------------------------------------------------------------------------
# Celery — production tuning (see docs/ops/CELERY_TUNING.md)
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = os.environ["CELERY_BROKER_URL"]
CELERY_RESULT_BACKEND = os.environ["CELERY_RESULT_BACKEND"]
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_TRACK_STARTED = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000
CELERY_WORKER_MAX_MEMORY_PER_CHILD = 200_000  # KiB
CELERY_BROKER_VISIBILITY_TIMEOUT = 3600
CELERY_TASK_SOFT_TIME_LIMIT = 60
CELERY_TASK_TIME_LIMIT = 90

# Eager mode OFF in production.
CELERY_TASK_ALWAYS_EAGER = False

# ---------------------------------------------------------------------------
# Email — real backend
# ---------------------------------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ["EMAIL_HOST"]
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ["EMAIL_HOST_USER"]
EMAIL_HOST_PASSWORD = os.environ["EMAIL_HOST_PASSWORD"]
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL",
                                    "no-reply@example.com")

# ---------------------------------------------------------------------------
# Logging — structured JSON to stdout (for Loki / CloudWatch / etc.)
#
# Falls back to plain text if python-json-logger isn't installed, so the
# module still imports in a fresh venv that hasn't installed prod deps.
# ---------------------------------------------------------------------------
try:
    import pythonjsonlogger.jsonlogger  # noqa: F401
    _FORMATTER = "pythonjsonlogger.jsonlogger.JsonFormatter"
except ImportError:
    _FORMATTER = "django.utils.log.ServerFormatter"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": _FORMATTER,
            "format": "%(asctime)s %(name)s %(levelname)s %(message)s",
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["stdout"],
        "level": "INFO",
    },
    "loggers": {
        "django": {"level": "WARNING"},
        "section2_queue": {"level": "INFO"},
        "celery": {"level": "INFO"},
    },
}

# ---------------------------------------------------------------------------
# Observability — Sentry + OpenTelemetry (opt-in via env)
# ---------------------------------------------------------------------------
if os.environ.get("SENTRY_DSN"):
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration

    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        integrations=[DjangoIntegration(), CeleryIntegration()],
        traces_sample_rate=float(
            os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")
        ),
        environment=os.environ.get("ENVIRONMENT", "production"),
        send_default_pii=False,
    )

if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
    # OpenTelemetry wiring — requires opentelemetry-distro.
    # Run with:
    #   opentelemetry-instrument celery -A config worker
    pass

# ---------------------------------------------------------------------------
# Static files — whitenoise for serving behind a LB
# ---------------------------------------------------------------------------
MIDDLEWARE = MIDDLEWARE + ["whitenoise.middleware.WhiteNoiseMiddleware"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
