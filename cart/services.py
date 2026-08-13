"""Order lifecycle service functions — currently just cancellation and its side effects."""

import logging

from django.conf import settings
from django.db import transaction

from common.emails import send_templated_email
from inventory.services import release_reservation, restore_stock_for_cancellation

from .models import Order

logger = logging.getLogger(__name__)


def cancel_order(order, *, cancelled_by=None, reason=""):
    """
    Cancels `order` if it's still in a cancellable state
    (`Order.CANCELLABLE_STATUSES`). Releases any held reservation for an
    unpaid order, or restores real stock for an already-paid one (payment
    capture already decremented it) — but deliberately does NOT touch the
    payment itself — refunding a captured payment stays a separate manual
    step via the Payments admin's "Initiate refund" action; this only
    alerts staff that one is owed.

    Raises `ValueError` if the order isn't in a cancellable state.
    """
    if order.status not in Order.CANCELLABLE_STATUSES:
        raise ValueError(f"Order {order.order_number} can't be cancelled from status '{order.status}'.")

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status not in Order.CANCELLABLE_STATUSES:
            raise ValueError(f"Order {order.order_number} can't be cancelled from status '{order.status}'.")

        was_paid = order.status in Order.PAID_STATUSES
        if was_paid:
            restore_stock_for_cancellation(order)
        else:
            release_reservation(order)

        order._status_change_actor = cancelled_by
        order._status_change_reason = reason or "Cancelled"
        order.status = Order.Status.CANCELLED
        order.save(update_fields=["status", "updated_at"])

    if was_paid:
        _notify_staff_refund_needed(order, cancelled_by, reason)

    return order


def accept_order(order, *, actor=None):
    """
    Staff commits to fulfilling a payment-confirmed order — it sits in
    PAYMENT_CONFIRMED as its own queue until then.
    """
    return _advance_order_status(
        order, from_status=Order.Status.PAYMENT_CONFIRMED, to_status=Order.Status.ACCEPTED, actor=actor
    )


def mark_order_packed(order, *, actor=None):
    """Order has been boxed and is ready to hand off to a courier."""
    return _advance_order_status(order, from_status=Order.Status.ACCEPTED, to_status=Order.Status.PACKED, actor=actor)


def _advance_order_status(order, *, from_status, to_status, actor):
    """
    Shared implementation for the staff-driven fulfillment-prep stage
    transitions above (accept_order/mark_order_packed). Raises ValueError
    if `order` isn't currently in `from_status`.

    These are internal bookkeeping stages, not customer-facing milestones
    — `_skip_status_email` suppresses the generic order-status-changed
    email (unlike payment/shipment-driven transitions, which already send
    their own purpose-built email) so a buyer isn't emailed on every
    internal step of a small operator's packing queue. The
    OrderStatusHistory audit trail is still written unconditionally
    regardless — see cart.signals.
    """
    if order.status != from_status:
        raise ValueError(
            f"Order {order.order_number} must be '{from_status}' to move to '{to_status}' "
            f"(currently '{order.status}')."
        )

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status != from_status:
            raise ValueError(
                f"Order {order.order_number} must be '{from_status}' to move to '{to_status}' "
                f"(currently '{order.status}')."
            )
        order._status_change_actor = actor
        order._skip_status_email = True
        order.status = to_status
        order.save(update_fields=["status", "updated_at"])
    return order


def _notify_staff_refund_needed(order, cancelled_by, reason):
    send_templated_email(
        "order_cancelled_refund_needed",
        {"order": order, "cancelled_by": cancelled_by, "reason": reason},
        to=settings.STAFF_NOTIFICATION_EMAIL,
    )
