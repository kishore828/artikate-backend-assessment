"""
Django project package for the Artikate Studio backend assessment.

The project is intentionally named ``config`` (rather than something
domain-specific) so that the project root stays framework-agnostic and
all four assessment sections live as first-class Django apps alongside it.
"""

from .celery import app as celery_app

__all__ = ("celery_app",)
