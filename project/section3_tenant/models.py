"""Section 3 — models.

* ``Tenant``  — a SaaS customer.
* ``Order``   — a tenant-scoped order, with two managers:
    * ``objects``   = TenantManager (auto-scoped to current tenant)
    * ``unscoped``  = UnscopedManager (raw access — use deliberately)
"""

from django.db import models

from .managers import TenantManager, UnscopedManager


class Tenant(models.Model):
    name = models.CharField(max_length=120)
    # Stable external identifier — what the middleware looks up by
    # when the request comes in with an `X-Tenant-ID` header.
    slug = models.SlugField(max_length=80, unique=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Tenant({self.slug})"


class Order(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    placed_at = models.DateTimeField(auto_now_add=True)
    total_cents = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        default="pending",
        choices=[
            ("pending", "Pending"),
            ("paid", "Paid"),
            ("shipped", "Shipped"),
        ],
    )

    # --- Managers --------------------------------------------------------
    # Order matters: the FIRST manager declared becomes the default
    # ``_default_manager`` used by Django internals (admin, reverse FK
    # lookups, etc.). We want the auto-scoping TenantManager to be the
    # default so even Django's own machinery cannot leak cross-tenant.
    objects = TenantManager()
    unscoped = UnscopedManager()

    class Meta:
        ordering = ["-placed_at"]
        # Composite index because tenant-scoped queries always filter on
        # tenant_id first.
        indexes = [
            models.Index(fields=["tenant", "-placed_at"]),
        ]

    def __str__(self) -> str:
        return f"Order #{self.pk} [{self.tenant.slug}]"
