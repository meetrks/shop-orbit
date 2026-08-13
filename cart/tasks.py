"""
Periodic sweep that formally cancels orders whose payment never completed.

Distinct from `inventory.tasks.release_expired_reservations`, which only
lets go of an abandoned checkout's stock hold after
`RESERVATION_TIMEOUT_MINUTES` and marks it EXPIRED — that leaves the order
itself open. A `PAYMENT_FAILED` order in particular never expires on its
own (its reservation was already released by `payments.pipeline
.on_payment_failed`), so without this sweep it would sit open forever
unless the buyer manually retries or cancels. This closes out anything
still `AWAITING_PAYMENT` or `PAYMENT_FAILED` after
`ORDER_CANCEL_TIMEOUT_HOURS`, via the same `cart.services.cancel_order`
path a buyer or staff member would use, so it gets the usual audit trail
and status-change email.

Scheduled via `CELERY_BEAT_SCHEDULE` in `config/settings/base.py`.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import Order
from .services import cancel_order

logger = logging.getLogger(__name__)


@shared_task
def cancel_stale_unpaid_orders():
    cutoff = timezone.now() - timezone.timedelta(hours=settings.ORDER_CANCEL_TIMEOUT_HOURS)
    stale_orders = Order.objects.filter(
        status__in=(Order.Status.AWAITING_PAYMENT, Order.Status.PAYMENT_FAILED),
        created_at__lt=cutoff,
    )

    cancelled_count = 0
    for order in stale_orders:
        try:
            cancel_order(
                order,
                reason="Automatically cancelled — payment wasn't completed within "
                f"{settings.ORDER_CANCEL_TIMEOUT_HOURS} hours.",
            )
        except ValueError:
            continue  # moved on (paid, already cancelled, ...) between the query above and now
        cancelled_count += 1

    if cancelled_count:
        logger.info("Automatically cancelled %s stale unpaid order(s).", cancelled_count)
    return cancelled_count
