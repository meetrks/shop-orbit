"""
Periodic sweep that releases stock reservations for checkouts nobody ever
finished — the buyer never opened Razorpay Checkout, closed the tab
mid-payment, or the payment simply never resolved either way. Without
this, that stock would stay held indefinitely, since the normal release
paths (`payments.pipeline.on_payment_failed`, `cart.services.cancel_order`)
only fire when something actually reports a definitive outcome.

Scheduled via `CELERY_BEAT_SCHEDULE` in `config/settings.py` — run a
worker with beat enabled locally (`celery -A config worker -B -l info`),
or `celery -A config beat -l info` as a separate process in production.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from cart.models import Order

from .services import release_reservation

logger = logging.getLogger(__name__)


@shared_task
def release_expired_reservations():
    cutoff = timezone.now() - timezone.timedelta(minutes=settings.RESERVATION_TIMEOUT_MINUTES)
    expired_orders = Order.objects.filter(status=Order.Status.AWAITING_PAYMENT, created_at__lt=cutoff)

    released_count = 0
    for order in expired_orders:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order.pk)
            if order.status != Order.Status.AWAITING_PAYMENT:
                continue  # captured/failed/cancelled in the gap between the query above and this lock
            release_reservation(order)
            order.status = Order.Status.EXPIRED
            order.save(update_fields=["status", "updated_at"])
        released_count += 1

    if released_count:
        logger.info("Released stock reservations for %s expired order(s).", released_count)
    return released_count
