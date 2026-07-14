from django.contrib import admin

from .models import Customer, Item, Order


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "email")
    search_fields = ("name", "email")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "placed_at", "total_cents")
    list_select_related = ("customer",)  # avoid N+1 in admin list view
    search_fields = ("id", "customer__name")


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "name", "price_cents", "quantity")
    list_select_related = ("order",)
