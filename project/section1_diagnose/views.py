"""Section 1 — views.

There are three endpoints:

1. ``/api/orders/summary/``            — *deliberately broken* (N+1).
2. ``/api/orders/summary/optimized/``  — fixed with ``prefetch_related``.
3. ``/api/orders/seed/``               — helper to populate 200+ orders so
   the broken view actually times out / shows up in django-silk.

The "broken" endpoint loops over each order and calls ``order.items.all()``
which fires a fresh SQL query per order. With 200 orders that is
200+1 round-trips — exactly the scenario described in the assessment brief.
"""

from django.db import connection
from django.http import JsonResponse

from .models import Customer, Item, Order


# ---------------------------------------------------------------------------
# 1. BROKEN view — N+1 query
# ---------------------------------------------------------------------------
def order_summary_broken(request):
    """Return per-order totals for the dashboard — N+1 style.

    For every Order we call ``order.items.all()`` which Django evaluates
    lazily as a *new* SQL query. With 200+ orders this means 1 query to
    fetch the orders + 200 queries to fetch each order's items = 201
    queries total. On the assessment's mobile-dashboard scenario this
    pushed response time from ~80ms to 30+ seconds.
    """
    orders = Order.objects.all()[:250]

    data = []
    for order in orders:
        # ``order.items.all()`` is the N+1 culprit. Each call is a fresh
        # SELECT ... FROM section1_diagnose_item WHERE order_id = <pk>.
        items = list(order.items.all())
        total_cents = sum(
            item.price_cents * item.quantity for item in items
        )
        data.append({
            "order_id": order.pk,
            "customer": order.customer.name,
            "item_count": len(items),
            "total_cents": total_cents,
        })

    return JsonResponse({
        "results": data,
        "query_count": len(connection.queries),
    })


# ---------------------------------------------------------------------------
# 2. FIXED view — prefetch_related collapses the N+1 into a single IN query
# ---------------------------------------------------------------------------
def order_summary_optimized(request):
    """Same payload, fixed with ``prefetch_related('items')``.

    ``prefetch_related`` runs *two* queries total:
      1. SELECT * FROM section1_diagnose_order LIMIT 250;
      2. SELECT * FROM section1_diagnose_item
            WHERE order_id IN (1, 2, 3, ..., 250);
    Django then stashes the items in a per-order cache, so
    ``order.items.all()`` inside the loop becomes a free in-memory lookup.

    We also use ``select_related('customer')`` for the forward FK so the
    customer name access doesn't add a third round-trip per order.
    """
    orders = (
        Order.objects
        .select_related("customer")          # 1 query (JOIN)
        .prefetch_related("items")           # 1 query (IN)
    )[:250]

    data = []
    for order in orders:
        # No extra SQL — items come from the prefetched cache.
        items = list(order.items.all())
        total_cents = sum(
            item.price_cents * item.quantity for item in items
        )
        data.append({
            "order_id": order.pk,
            "customer": order.customer.name,
            "item_count": len(items),
            "total_cents": total_cents,
        })

    return JsonResponse({
        "results": data,
        "query_count": len(connection.queries),
    })


# ---------------------------------------------------------------------------
# 3. Seed helper — populates 220 orders × 3 items each
# ---------------------------------------------------------------------------
def seed(request):
    """Populate the DB with 220 orders × 3 items each.

    Hit this once before profiling so the broken view has enough data to
    make the N+1 obvious in django-silk.

    Idempotent: clears existing rows first so re-running doesn't pile up.
    """
    Item.objects.all().delete()
    Order.objects.all().delete()
    Customer.objects.all().delete()

    customer = Customer.objects.create(
        name="Demo Customer", email="demo@example.com"
    )

    orders = [
        Order(customer=customer, total_cents=300)
        for _ in range(220)
    ]
    Order.objects.bulk_create(orders)

    items = []
    for order in orders:
        for name in ("Widget", "Gadget", "Gizmo"):
            items.append(Item(
                order=order,
                name=name,
                price_cents=100,
                quantity=1,
            ))
    Item.objects.bulk_create(items)

    return JsonResponse({
        "created": {
            "customers": 1,
            "orders": len(orders),
            "items": len(items),
        }
    })
