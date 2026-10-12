from django.apps import AppConfig


class ConfigConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "config"
    verbose_name = "Configuration (settings & commission)"

    def ready(self):
        from . import definitions  # noqa: F401  (registers every tunable setting)
