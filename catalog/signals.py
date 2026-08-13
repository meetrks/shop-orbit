"""
Detects a 0 -> positive stock_count transition on Product or ProductVariant
and emails everyone with a pending StockAlert for it. Staff only ever edit
stock_count from the Django admin today, so a signal is the
only place that reliably sees every change regardless of which admin
form (Product's own field, or the ProductVariant inline) touched it.

Also keeps `Product.search_vector` (see catalog.search) in sync on every
save, via a queryset `.update()` rather than `instance.save()` so this
doesn't recurse back through the same signal.
"""

from django.contrib.postgres.search import SearchVector
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .emails import notify_back_in_stock
from .models import Product, ProductVariant


def _restocked(previous_stock, new_stock):
    return previous_stock is not None and previous_stock <= 0 and new_stock > 0


@receiver(pre_save, sender=Product)
def _cache_previous_product_stock(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_stock_count = None
        return
    try:
        instance._previous_stock_count = Product.objects.get(pk=instance.pk).stock_count
    except Product.DoesNotExist:
        instance._previous_stock_count = None


@receiver(post_save, sender=Product)
def _notify_product_restocked(sender, instance, created, **kwargs):
    if created:
        return
    if _restocked(getattr(instance, "_previous_stock_count", None), instance.stock_count):
        transaction.on_commit(lambda: notify_back_in_stock(instance, variant=None))


@receiver(pre_save, sender=ProductVariant)
def _cache_previous_variant_stock(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_stock_count = None
        return
    try:
        instance._previous_stock_count = ProductVariant.objects.get(pk=instance.pk).stock_count
    except ProductVariant.DoesNotExist:
        instance._previous_stock_count = None


@receiver(post_save, sender=ProductVariant)
def _notify_variant_restocked(sender, instance, created, **kwargs):
    if created:
        return
    if _restocked(getattr(instance, "_previous_stock_count", None), instance.stock_count):
        transaction.on_commit(lambda: notify_back_in_stock(instance.product, variant=instance))


@receiver(post_save, sender=Product)
def _update_search_vector(sender, instance, **kwargs):
    Product.objects.filter(pk=instance.pk).update(
        search_vector=(
            SearchVector("title", weight="A")
            + SearchVector("meta_keywords", weight="B")
            + SearchVector("description", weight="C")
        )
    )
