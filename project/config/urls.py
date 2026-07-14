"""Root URL configuration for the ``config`` project.

URL map
-------
* ``/admin/``                              — Django admin
* ``/api/orders/summary/``                 — Section 1 broken view (N+1)
* ``/api/orders/summary/optimized/``       — Section 1 fixed view (prefetch_related)
* ``/api/orders/seed/``                    — Helper to populate demo data (200+ orders)
* ``/healthz/``                            — Liveness probe (always 200)
* ``/readyz/``                             — Readiness probe (checks DB + Redis)
* ``/silk/``                               — Section 1 profiler UI
"""

from django.contrib import admin
from django.urls import include, path

from .health_views import healthz, readyz

urlpatterns = [
    path("admin/", admin.site.urls),

    # Section 1: diagnose / fix N+1 queries
    path("api/orders/", include("section1_diagnose.urls")),

    # Health / readiness probes (used by docker-compose healthchecks
    # and load balancers).
    path("healthz/", healthz, name="healthz"),
    path("readyz/", readyz, name="readyz"),

    # Section 1: django-silk profiler
    path("silk/", include("silk.urls", namespace="silk")),
]
