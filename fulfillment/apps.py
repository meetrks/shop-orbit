from django.apps import AppConfig


class FulfillmentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fulfillment"

    def ready(self):
        from . import signals  # noqa: F401
