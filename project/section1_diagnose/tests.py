"""Tests for Section 1 — the N+1 diagnosis.

What we prove
-------------
1. The broken view actually fires one extra SQL query *per order* when
   looping — i.e. the N+1 is real and reproducible.
2. The fixed view (``prefetch_related`` + ``select_related``) collapses
   to exactly **2** queries regardless of how many orders exist.
3. Both views return the same payload — the fix is purely a DB-roundtrip
   optimisation, not a behaviour change.

NOTE on django-silk
-------------------
Silk's middleware runs ``EXPLAIN`` on every query to capture its query
plan — which doubles the entries in ``connection.queries``. We filter
those out (``_real_queries``) so the assertions reflect what Django's
ORM actually sent to the database.
"""

from django.db import connection, reset_queries
from django.test import TestCase, override_settings
from django.urls import reverse

from section1_diagnose.models import Customer, Item, Order


def _real_queries():
    """Return ``connection.queries`` minus Silk's EXPLAIN noise."""
    return [q for q in connection.queries
            if not q["sql"].lstrip().upper().startswith("EXPLAIN")]


def _seed_orders(n: int = 25) -> None:
    """Create ``n`` orders with 3 items each — enough to make the N+1
    visible without slowing the test suite."""
    import uuid
    customer = Customer.objects.create(
        name="Test Customer", email=f"t-{uuid.uuid4().hex[:8]}@example.com"
    )
    orders = [Order(customer=customer, total_cents=300) for _ in range(n)]
    Order.objects.bulk_create(orders)
    items = []
    for order in orders:
        for name in ("Widget", "Gadget", "Gizmo"):
            items.append(Item(
                order=order, name=name, price_cents=100, quantity=1
            ))
    Item.objects.bulk_create(items)


# Disable Silk for these tests so its EXPLAIN probes don't pollute
# connection.queries. The profiler is still useful in dev — we just
# turn it off in the test runner to get clean query counts.
@override_settings(MIDDLEWARE=[
    m for m in __import__("django.conf", fromlist=["settings"]).settings.MIDDLEWARE
    if "silk.middleware.SilkyMiddleware" not in m
])
class BrokenViewTests(TestCase):
    """Prove the broken view fires N+1 queries."""

    def setUp(self):
        _seed_orders(n=25)
        # Django's DEBUG=False suppresses connection.queries, so flip it
        # on for the duration of these tests.
        from django.conf import settings
        self._old_debug = settings.DEBUG
        settings.DEBUG = True
        connection.force_debug_cursor = True

    def tearDown(self):
        from django.conf import settings
        settings.DEBUG = self._old_debug
        connection.force_debug_cursor = False

    def test_broken_view_fires_n_plus_1_queries(self):
        reset_queries()
        response = self.client.get(reverse("order_summary_broken"))

        self.assertEqual(response.status_code, 200)
        queries = _real_queries()
        item_queries = [q for q in queries
                        if "section1_diagnose_item" in q["sql"]]
        # The smoking gun: one SELECT per order (25 total).
        self.assertEqual(len(item_queries), 25,
                         f"Expected 25 item queries (one per order), got "
                         f"{len(item_queries)}. Queries:\n"
                         + "\n".join(q["sql"] for q in item_queries[:5]))
        # Plus at least 1 query for orders + 25 for customer lazy-loads.
        self.assertGreaterEqual(len(queries), 26)


@override_settings(MIDDLEWARE=[
    m for m in __import__("django.conf", fromlist=["settings"]).settings.MIDDLEWARE
    if "silk.middleware.SilkyMiddleware" not in m
])
class OptimizedViewTests(TestCase):
    """Prove the fixed view fires exactly 2 queries."""

    def setUp(self):
        _seed_orders(n=25)
        from django.conf import settings
        self._old_debug = settings.DEBUG
        settings.DEBUG = True
        connection.force_debug_cursor = True

    def tearDown(self):
        from django.conf import settings
        settings.DEBUG = self._old_debug
        connection.force_debug_cursor = False

    def test_optimized_view_fires_constant_two_queries(self):
        reset_queries()
        response = self.client.get(reverse("order_summary_optimized"))

        self.assertEqual(response.status_code, 200)
        body = response.json()

        queries = _real_queries()
        order_queries = [q for q in queries
                         if "section1_diagnose_order" in q["sql"]]
        item_queries = [q for q in queries
                        if "section1_diagnose_item" in q["sql"]]
        # 1) SELECT orders JOIN customer
        # 2) SELECT items WHERE order_id IN (...)
        # That's it — constant, regardless of how many orders exist.
        self.assertEqual(len(order_queries), 1,
                         f"Expected 1 orders query, got {len(order_queries)}")
        self.assertEqual(len(item_queries), 1,
                         f"Expected 1 items query (prefetched), got "
                         f"{len(item_queries)}")
        self.assertGreaterEqual(len(body["results"]), 25)

    def test_optimized_view_scales_constantly_with_size(self):
        """Doubling the data should NOT double the query count."""
        _seed_orders(n=25)  # already had 25; now 50 total

        reset_queries()
        response = self.client.get(reverse("order_summary_optimized"))
        self.assertEqual(response.status_code, 200)

        queries = _real_queries()
        item_queries = [q for q in queries
                        if "section1_diagnose_item" in q["sql"]]
        # Still 1 item query — prefetch_related ran an IN (...) query.
        self.assertEqual(len(item_queries), 1)


@override_settings(MIDDLEWARE=[
    m for m in __import__("django.conf", fromlist=["settings"]).settings.MIDDLEWARE
    if "silk.middleware.SilkyMiddleware" not in m
])
class PayloadEquivalenceTests(TestCase):
    """Both views must return identical business data."""

    def setUp(self):
        _seed_orders(n=10)

    def test_both_views_return_same_results(self):
        broken = self.client.get(reverse("order_summary_broken")).json()
        fixed = self.client.get(reverse("order_summary_optimized")).json()

        # Sort by order_id to make the comparison order-independent.
        broken_sorted = sorted(broken["results"], key=lambda r: r["order_id"])
        fixed_sorted = sorted(fixed["results"], key=lambda r: r["order_id"])

        self.assertEqual(broken_sorted, fixed_sorted)
