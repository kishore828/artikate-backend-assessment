"""Section 2 — dead-letter model.

When a task exhausts its retry budget, instead of silently dropping the
job we persist the original payload + the final error here so an
operator can inspect / replay / re-queue it.

This is the simplest meaningful "dead-letter queue" implementation
inside Django: there is no separate dead-letter exchange (we are using
Redis as broker, not RabbitMQ), so we record the failure in the DB and
expose it through the admin.
"""

from django.db import models


class FailedTask(models.Model):
    """Permanently-failed task record (dead-letter)."""

    task_name = models.CharField(max_length=255)
    task_id = models.CharField(max_length=255, db_index=True)

    # Original invocation payload. Stored as JSON so we can replay the
    # task with `task.signature(args=..., kwargs=...).apply_async()`.
    args = models.JSONField(default=list)
    kwargs = models.JSONField(default=dict)

    # Final exception that caused the task to give up.
    exception_type = models.CharField(max_length=255)
    exception_message = models.TextField()
    traceback = models.TextField(blank=True, default="")

    retries_attempted = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["task_name", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.task_name}({self.task_id}) — {self.exception_type}"
