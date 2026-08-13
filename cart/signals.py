"""
Detects order status transitions so a notification email can be sent no
matter where the status changed — normally the Django admin (e.g. a staff
member assigning a shipment, see fulfillment/signals.py), but this also
covers management commands or a future staff API.

`payments.pipeline` sets `instance._skip_status_email = True` before
saving a payment/refund-driven transition (payment confirmed/failed,
refunded) — those already send their own richer, purpose-built email
(with the invoice link, failure reason, or refund amount) right after, so
this generic one would just be a duplicate for those transitions. The
`OrderStatusHistory` audit trail is written unconditionally regardless of
that flag — see `_record_status_history` below — since the audit trail
must be complete even for transitions whose buyer email is suppressed.

A caller can attribute a transition to whoever/whatever triggered it by
setting `instance._status_change_actor` (a `User`, or `None` for
system-triggered) and `instance._status_change_reason` (a short string)
before calling `.save()` — see `cart.services.cancel_order` for the
pattern. Both are optional; a plain admin edit or any other unattributed
save still gets a history row, just with a blank actor/reason.

New-order confirmation emails are NOT sent from here: at the moment an
`Order` row is first saved its `OrderItem`s don't exist yet (checkout
creates them in a follow-up loop within the same transaction), so that
email is sent explicitly once payment is confirmed — see
`payments.pipeline.on_payment_captured`. This module only ever sees status
changes on existing orders.
"""

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .emails import send_order_status_changed_email
from .models import Order, OrderStatusHistory


@receiver(pre_save, sender=Order)
def _cache_previous_status(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_status = None
        return
    try:
        instance._previous_status = Order.objects.get(pk=instance.pk).status
    except Order.DoesNotExist:
        instance._previous_status = None


@receiver(post_save, sender=Order)
def _record_status_history(sender, instance, created, **kwargs):
    if created:
        return
    previous_status = getattr(instance, "_previous_status", None)
    if previous_status and previous_status != instance.status:
        OrderStatusHistory.objects.create(
            order=instance,
            previous_status=previous_status,
            new_status=instance.status,
            changed_by=getattr(instance, "_status_change_actor", None),
            reason=getattr(instance, "_status_change_reason", ""),
        )


@receiver(post_save, sender=Order)
def _send_status_change_email(sender, instance, created, **kwargs):
    if created or getattr(instance, "_skip_status_email", False):
        return
    previous_status = getattr(instance, "_previous_status", None)
    if previous_status and previous_status != instance.status:
        transaction.on_commit(lambda: send_order_status_changed_email(instance, previous_status))
