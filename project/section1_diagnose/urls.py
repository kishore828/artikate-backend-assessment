"""URL routes for Section 1."""

from django.urls import path

from . import views

urlpatterns = [
    # Broken view — demonstrates N+1 query problem.
    path("summary/", views.order_summary_broken, name="order_summary_broken"),
    # Fixed view — same payload, prefetch_related eliminates the N+1.
    path("summary/optimized/", views.order_summary_optimized, name="order_summary_optimized"),
    # Seed helper — populates 220 orders × 3 items for profiling.
    path("seed/", views.seed, name="order_seed"),
]
