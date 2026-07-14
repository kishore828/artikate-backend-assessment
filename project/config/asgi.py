"""ASGI config for the ``config`` project.

ASGI is wired up so the same project can run under an async server
(Daphne / Uvicorn) — important for Section 3 where the brief explicitly
asks about async Django behaviour and why ``contextvars`` is the right
tool instead of ``threading.local``.
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
