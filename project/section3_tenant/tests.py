"""
Tests for Section 3 — multi-tenant data isolation.

What we prove (the brief asks for "tests proving the negative")
----------------------------------------------------------------
1. Tenant A's request sees ONLY tenant A's orders — never tenant B's.
2. Calling ``Order.objects.all()`` (the bypass the brief is worried
   about) returns ONLY tenant A's rows.
3. Tenant A cannot fetch tenant B's order by primary key — the manager
   filters BEFORE the lookup, so ``.get(pk=...)`` raises DoesNotExist.
4. ``Order.objects.filter(...)`` cannot escape the tenant scope, even
   with a deliberately loose filter.
5. With NO tenant in the context (e.g. a background job that forgot to
   bind one), the manager returns ZERO rows — fail-closed.
6. ``Order.unscoped`` is the only deliberate escape hatch.
7. Even inside a transaction, the scope applies.
8. Context cleanup: after a request finishes, the ContextVar is None
   again (no leak to the next request).
"""

from django.test import TestCase, override_settings

from section3_tenant.context import get_current_tenant, set_current_tenant
from section3_tenant.models import Order, Tenant


def _bind(tenant: Tenant):
    return set_current_tenant(tenant)


class TenantIsolationTests(TestCase):
    """The core negative-tests — proving isolation CANNOT be bypassed."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Acme", slug="acme")
        self.tenant_b = Tenant.objects.create(name="Globex", slug="globex")

        # 3 orders for A, 2 for B.
        for _ in range(3):
            Order.unscoped.create(tenant=self.tenant_a, total_cents=100)
        for _ in range(2):
            Order.unscoped.create(tenant=self.tenant_b, total_cents=200)

    # ------------------------------------------------------------------
    # (1) Tenant A sees only A's orders
    # ------------------------------------------------------------------
    def test_tenant_a_sees_only_own_orders(self):
        token = _bind(self.tenant_a)
        try:
            qs = Order.objects.all()
            self.assertEqual(qs.count(), 3)
            self.assertTrue(
                all(o.tenant_id == self.tenant_a.id for o in qs)
            )
        finally:
            from section3_tenant.context import reset_current_tenant
            reset_current_tenant(token)

    # ------------------------------------------------------------------
    # (2) .objects.all() does NOT bypass scoping
    # ------------------------------------------------------------------
    def test_objects_all_does_not_bypass_scoping(self):
        token = _bind(self.tenant_b)
        try:
            # Even though we call .all(), the manager pre-filtered.
            all_orders = list(Order.objects.all())
            self.assertEqual(len(all_orders), 2)
            for o in all_orders:
                self.assertEqual(o.tenant_id, self.tenant_b.id)
        finally:
            from section3_tenant.context import reset_current_tenant
            reset_current_tenant(token)

    # ------------------------------------------------------------------
    # (3) Cross-tenant .get(pk=...) raises DoesNotExist
    # ------------------------------------------------------------------
    def test_cross_tenant_get_raises_does_not_exist(self):
        # Pick an order that belongs to B.
        b_order = Order.unscoped.filter(tenant=self.tenant_b).first()
        token = _bind(self.tenant_a)
        try:
            with self.assertRaises(Order.DoesNotExist):
                Order.objects.get(pk=b_order.pk)
        finally:
            from section3_tenant.context import reset_current_tenant
            reset_current_tenant(token)

    # ------------------------------------------------------------------
    # (4) Loose filter cannot escape the tenant
    # ------------------------------------------------------------------
    def test_loose_filter_cannot_escape_tenant(self):
        token = _bind(self.tenant_a)
        try:
            # No tenant filter — but manager still scopes.
            qs = Order.objects.filter(total_cents__gte=0)
            self.assertEqual(qs.count(), 3)
            # All 3 belong to A.
            tenants = {o.tenant_id for o in qs}
            self.assertEqual(tenants, {self.tenant_a.id})
        finally:
            from section3_tenant.context import reset_current_tenant
            reset_current_tenant(token)

    # ------------------------------------------------------------------
    # (5) No tenant in context → fail closed (zero rows)
    # ------------------------------------------------------------------
    def test_no_tenant_in_context_returns_zero_rows(self):
        # Default ContextVar is None — manager returns .none()
        self.assertIsNone(get_current_tenant())
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(list(Order.objects.all()), [])
        self.assertFalse(Order.objects.exists())

    # ------------------------------------------------------------------
    # (6) Unscoped is the deliberate escape hatch
    # ------------------------------------------------------------------
    def test_unscoped_manager_sees_all_tenants(self):
        # No tenant bound — but unscoped ignores the contextvar.
        self.assertEqual(Order.unscoped.count(), 5)

    # ------------------------------------------------------------------
    # (7) Scope applies inside a transaction
    # ------------------------------------------------------------------
    def test_scope_applies_inside_transaction(self):
        from django.db import transaction
        token = _bind(self.tenant_a)
        try:
            with transaction.atomic():
                self.assertEqual(Order.objects.count(), 3)
                # Even a freshly-created B order is invisible inside this tx.
                Order.unscoped.create(tenant=self.tenant_b, total_cents=999)
                self.assertEqual(Order.objects.count(), 3)  # still 3
        finally:
            from section3_tenant.context import reset_current_tenant
            reset_current_tenant(token)

    # ------------------------------------------------------------------
    # (8) Context cleanup — no leak to next request
    # ------------------------------------------------------------------
    def test_context_is_cleared_after_reset(self):
        token = _bind(self.tenant_a)
        self.assertIsNotNone(get_current_tenant())
        from section3_tenant.context import reset_current_tenant
        reset_current_tenant(token)
        self.assertIsNone(get_current_tenant())


class TenantMiddlewareTests(TestCase):
    """End-to-end test of the middleware against a fake view.

    We use Django's ``override_settings(ROOT_URLCONF=...)`` to install a
    tiny temporary URLconf module for the duration of each test. This is
    the supported way to add URLs on the fly — much cleaner than
    monkey-patching ``sys.modules``.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Build a real, importable module so override_settings can
        # resolve it by dotted path.
        import sys
        import types
        from django.http import JsonResponse
        from django.urls import path

        def count_view(request):
            return JsonResponse({"count": Order.objects.count()})

        def who_view(request):
            return JsonResponse({
                "slug": request.tenant.slug if request.tenant else None,
            })

        mod = types.ModuleType("section3_tenant._test_urls")
        mod.urlpatterns = [
            path("count/", count_view),
            path("who/", who_view),
        ]
        sys.modules["section3_tenant._test_urls"] = mod
        cls._test_urls = mod

    @classmethod
    def tearDownClass(cls):
        import sys
        sys.modules.pop("section3_tenant._test_urls", None)
        super().tearDownClass()

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Acme", slug="acme")
        self.tenant_b = Tenant.objects.create(name="Globex", slug="globex")
        Order.unscoped.create(tenant=self.tenant_a, total_cents=100)
        Order.unscoped.create(tenant=self.tenant_b, total_cents=200)

    @override_settings(ROOT_URLCONF="section3_tenant._test_urls")
    def test_request_with_tenant_header_scopes_orm(self):
        from django.test import Client
        r = Client().get("/count/", HTTP_X_TENANT_ID="acme")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 1)

    @override_settings(ROOT_URLCONF="section3_tenant._test_urls")
    def test_unknown_tenant_header_returns_400(self):
        from django.test import Client
        r = Client().get("/count/", HTTP_X_TENANT_ID="nonexistent")
        self.assertEqual(r.status_code, 400)

    @override_settings(ROOT_URLCONF="section3_tenant._test_urls")
    def test_context_cleared_between_requests(self):
        """Two consecutive requests with different tenants must not leak."""
        from django.test import Client
        client = Client()
        r1 = client.get("/who/", HTTP_X_TENANT_ID="acme")
        r2 = client.get("/who/", HTTP_X_TENANT_ID="globex")
        self.assertEqual(r1.json()["slug"], "acme")
        self.assertEqual(r2.json()["slug"], "globex")
