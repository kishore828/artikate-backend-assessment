"""Models for Section 1 — the "broken" e-commerce dashboard.

Schema
------
* ``Customer``  — a buyer (added so the demo has a realistic third table).
* ``Order``     — a purchase placed by a customer.
* ``Item``      — a line item inside an order (FK -> Order, related_name='items').

The N+1 happens in the *reverse* direction (Order -> Items), so the
correct fix is ``prefetch_related('items')`` rather than
``select_related('items')`` (the latter only works for forward FK / OneToOne).
"""

from django.db import models


class Customer(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField(unique=True)

    def __str__(self) -> str:
        return self.name


class Order(models.Model):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    placed_at = models.DateTimeField(auto_now_add=True)
    # Cached denormalised total — the dashboard reads this in the fixed view
    # but the *broken* view deliberately recomputes it from items to trigger
    # the N+1.
    total_cents = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-placed_at"]

    def __str__(self) -> str:
        return f"Order #{self.pk} ({self.customer.name})"


class Item(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    name = models.CharField(max_length=120)
    price_cents = models.PositiveIntegerField()
    quantity = models.PositiveIntegerField(default=1)

    def __str__(self) -> str:
        return f"{self.quantity}× {self.name}"
