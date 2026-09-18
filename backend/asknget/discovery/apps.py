from django.apps import AppConfig


class DiscoveryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "discovery"
    verbose_name = "Search & recommendations"

    def ready(self):
        from . import signals  # noqa: F401  (marks embeddings stale on catalog edits)
