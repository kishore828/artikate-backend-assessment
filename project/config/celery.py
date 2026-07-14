"""Celery application bootstrap for the ``config`` Django project.

This module is imported by ``config/__init__.py`` so that the Celery app
is always available whenever Django starts — including from the test
runner, the runserver, and management commands. ``autodiscover_tasks``
walks every INSTALLED_APP looking for a ``tasks.py`` module and registers
anything decorated with ``@shared_task`` / ``@app.task``.
"""

import os

from celery import Celery

# Make sure Django settings are loadable before Celery reads them.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")

# Read all CELERY_* settings from Django's settings module.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks defined inside each app's tasks.py module.
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):  # pragma: no cover - sanity-check task
    """Print the task request — useful for `celery -A config inspect`."""
    print(f"Request: {self.request!r}")
