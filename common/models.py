"""
Shared abstract base models used across every shoporbit app.

This module is a plain Python package (not a Django "app" listed in
INSTALLED_APPS) because `TimeStampedModel` is declared abstract and never
instantiated on its own — it only ever contributes fields to concrete
models defined in accounts, catalog, cart, and picweight, each of which
already carries its own app label and migrations.
"""

from django.db import models


class TimeStampedModel(models.Model):
    """
    Abstract base class that adds self-managed `created_at` and
    `updated_at` timestamp columns to any model that inherits from it.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]
