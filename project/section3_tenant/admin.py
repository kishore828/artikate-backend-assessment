from django.contrib import admin

from .models import Order, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug", "created_at")
    search_fields = ("name", "slug")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "status", "total_cents", "placed_at")
    list_filter = ("status", "tenant")
    list_select_related = ("tenant",)
    search_fields = ("id", "tenant__slug")

    def get_queryset(self, request):
        # Admin superusers see everything; non-superusers see only the
        # tenant bound by the middleware.
        if request.user.is_superuser:
            return Order.unscoped.all()
        return super().get_queryset(request)
