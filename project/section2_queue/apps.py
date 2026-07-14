from django.apps import AppConfig


class Section2QueueConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "section2_queue"
    verbose_name = "Section 2 — Rate-Limited Async Job Queue"
