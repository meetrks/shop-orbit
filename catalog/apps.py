from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"

    def ready(self):
        import eav

        from . import signals  # noqa: F401
        from .models import Product

        eav.register(Product)
