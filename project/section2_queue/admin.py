from django.contrib import admin

from .models import FailedTask


@admin.register(FailedTask)
class FailedTaskAdmin(admin.ModelAdmin):
    list_display = (
        "task_name", "task_id", "exception_type", "retries_attempted",
        "created_at",
    )
    list_filter = ("task_name", "exception_type", "created_at")
    search_fields = ("task_id", "exception_message")
    readonly_fields = (
        "task_name", "task_id", "args", "kwargs",
        "exception_type", "exception_message", "traceback",
        "retries_attempted", "created_at",
    )
