"""
Section 3 — TenantManager.

This is the heart of the multi-tenant isolation contract.

Contract
--------
* ``Order.objects.all()``  → ONLY the current tenant's orders.
* ``Order.objects.filter(...)`` → filtered *within* the current tenant.
* ``Order.objects.get(pk=...)`` → raises DoesNotExist if the pk belongs
  to another tenant (because the queryset is pre-filtered).
* If no tenant is set in the context (e.g. a management command, a
  stray background job), we return ``.none()`` rather than returning
  every row in the table. This is the **fail-closed** posture the brief
  demands — "a single missed .filter() call must never expose another
  tenant's data".

Why a custom Manager and not just middleware that injects a filter?
-------------------------------------------------------------------
A manager-level override is enforced on *every* ORM entry point that
goes through the default manager — `.all()`, `.filter()`, `.get()`,
`.exclude()`, `.count()`, `.exists()`, `.first()`, etc. Middleware
that rewrites querysets only protects code that *uses* the rewritten
queryset; any code that calls ``Order.objects...`` directly bypasses it.
The manager is the only place where scoping is unavoidable.
"""

from __future__ import annotations

from django.db import models

from .context import get_current_tenant


class TenantManager(models.Manager):
    """Auto-scoping manager — filters by the current tenant on every query.

    For raw/unscoped access (e.g. migrations, admin superuser views,
    management commands) use ``TenantManager.unscoped()`` via the
    ``unscoped`` manager alias declared on each model.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        tenant = get_current_tenant()
        if tenant is None:
            # Fail closed: no tenant context = no rows. This is what
            # makes the manager *impossible to accidentally bypass* —
            # even calling `.objects.all()` from a context without a
            # tenant returns nothing, rather than the entire table.
            return qs.none()
        return qs.filter(tenant=tenant)


class UnscopedManager(models.Manager):
    """Escape hatch — explicit, auditable, and used only where scoping
    MUST be bypassed (e.g. migrations, superuser admin, tenant bootstrap).

    Named ``unscoped`` rather than ``all_objects`` to make its use
    visually loud in code review."""
    pass
