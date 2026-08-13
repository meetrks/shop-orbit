"""
Return-request workflow: requested -> approved/rejected -> (staff
physically receive the item back) -> received -> refund initiated ->
refunded. See returns/models.py for the full state machine.

Refunding itself is *not* reimplemented here — `initiate_return_refund`
calls the same `payments.services.PaymentService.initiate_refund` every
other refund in the system goes through, just with `return_request=` set
so the two stay linked (see `payments.pipeline` for what happens once
Razorpay confirms it).
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from cart.models import Order
from inventory.services import restock_from_return

from .emails import send_return_approved_email, send_return_rejected_email, send_return_requested_email
from .models import ReturnRequest, ReturnRequestLine, ReturnShipment

logger = logging.getLogger(__name__)


def _already_returned_quantity(order_item):
    """Total quantity of `order_item` covered by any non-rejected/non-cancelled return request line."""
    return (
        ReturnRequestLine.objects.filter(order_item=order_item)
        .exclude(return_request__status__in=[ReturnRequest.Status.REJECTED, ReturnRequest.Status.CANCELLED])
        .aggregate(total=Sum("quantity"))["total"]
        or 0
    )


def remaining_returnable_quantity(order_item):
    """How many units of `order_item` are still eligible to be returned."""
    return order_item.quantity - _already_returned_quantity(order_item)


def _return_window_end(order):
    shipment = getattr(order, "shipment", None)
    if not shipment or not shipment.delivered_at:
        return None
    return shipment.delivered_at + timedelta(days=settings.RETURN_WINDOW_DAYS)


def is_order_returnable(order):
    """Whether `order` is currently eligible for a new return request — gates the buyer-facing button."""
    if order.status != Order.Status.DELIVERED:
        return False
    window_end = _return_window_end(order)
    if window_end is None or timezone.now() > window_end:
        return False
    return any(remaining_returnable_quantity(item) > 0 for item in order.items.all())


def request_return(order, lines_data, *, reason, comments="", requested_by):
    """
    `lines_data`: iterable of `{"order_item": OrderItem, "quantity": int}`.
    Raises `ValueError` if the order isn't eligible (not delivered,
    outside the return window) or any line's quantity can't be covered.
    """
    if order.status != Order.Status.DELIVERED:
        raise ValueError("Only delivered orders can be returned.")

    window_end = _return_window_end(order)
    if window_end is None:
        raise ValueError("This order has no recorded delivery date.")
    if timezone.now() > window_end:
        raise ValueError(f"The {settings.RETURN_WINDOW_DAYS}-day return window for this order has passed.")

    lines_data = list(lines_data)
    if not lines_data:
        raise ValueError("Select at least one item to return.")

    for line in lines_data:
        order_item = line["order_item"]
        quantity = line["quantity"]
        if order_item.order_id != order.pk:
            raise ValueError("That item isn't part of this order.")
        remaining = remaining_returnable_quantity(order_item)
        if quantity <= 0 or quantity > remaining:
            raise ValueError(f'Can\'t return {quantity} of "{order_item.product_title}" — only {remaining} eligible.')

    with transaction.atomic():
        return_request = ReturnRequest.objects.create(order=order, user=requested_by, reason=reason, comments=comments)
        for line in lines_data:
            ReturnRequestLine.objects.create(
                return_request=return_request, order_item=line["order_item"], quantity=line["quantity"]
            )

    send_return_requested_email(return_request)
    return return_request


def approve_return(return_request, *, reviewed_by, staff_notes=""):
    if return_request.status != ReturnRequest.Status.REQUESTED:
        raise ValueError(f"Return {return_request.pk} can't be approved from status '{return_request.status}'.")
    return_request.status = ReturnRequest.Status.APPROVED
    return_request.reviewed_by = reviewed_by
    return_request.reviewed_at = timezone.now()
    if staff_notes:
        return_request.staff_notes = staff_notes
    return_request.save(update_fields=["status", "reviewed_by", "reviewed_at", "staff_notes", "updated_at"])
    ReturnShipment.objects.get_or_create(return_request=return_request)
    send_return_approved_email(return_request)
    return return_request


def reject_return(return_request, *, reviewed_by, staff_notes=""):
    if return_request.status != ReturnRequest.Status.REQUESTED:
        raise ValueError(f"Return {return_request.pk} can't be rejected from status '{return_request.status}'.")
    return_request.status = ReturnRequest.Status.REJECTED
    return_request.reviewed_by = reviewed_by
    return_request.reviewed_at = timezone.now()
    if staff_notes:
        return_request.staff_notes = staff_notes
    return_request.save(update_fields=["status", "reviewed_by", "reviewed_at", "staff_notes", "updated_at"])
    send_return_rejected_email(return_request)
    return return_request


def mark_return_received(return_request, *, line_decisions, received_by=None):
    """
    `line_decisions`: `{line_id: {"restock": bool, "condition_note": str}}`
    — a per-line judgment call, since not everything that comes back is
    resellable. A line missing from `line_decisions` defaults to *not*
    restocked (the safer default — an explicit choice is required to put
    something back on the shelf).
    """
    if return_request.status != ReturnRequest.Status.APPROVED:
        raise ValueError(
            f"Return {return_request.pk} must be Approved before it can be received "
            f"(currently '{return_request.status}')."
        )

    with transaction.atomic():
        for line in return_request.lines.select_related("order_item__product", "order_item__variant"):
            decision = line_decisions.get(line.pk, {})
            should_restock = bool(decision.get("restock"))
            condition_note = decision.get("condition_note", "")

            if should_restock and line.order_item.product_id:
                restock_from_return(
                    line.order_item.product,
                    variant=line.order_item.variant,
                    quantity=line.quantity,
                    return_request=return_request,
                    note=condition_note or f"Returned in {return_request}",
                    created_by=received_by,
                )
            elif should_restock:
                # The product this line pointed at has since been deleted
                # (OrderItem.product is SET_NULL) — nothing to restock against.
                logger.warning("Return line %s wanted to restock but its product no longer exists; skipping.", line.pk)
                should_restock = False

            line.restocked = should_restock
            line.condition_note = condition_note
            line.save(update_fields=["restocked", "condition_note", "updated_at"])

        return_request.status = ReturnRequest.Status.RECEIVED
        return_request.received_at = timezone.now()
        return_request.save(update_fields=["status", "received_at", "updated_at"])

        # Physically receiving the parcel back *is* the delivery event for
        # its return shipment — no separate staff step needed to mark it.
        return_shipment = getattr(return_request, "return_shipment", None)
        if return_shipment and return_shipment.status != ReturnShipment.Status.DELIVERED:
            return_shipment.status = ReturnShipment.Status.DELIVERED
            return_shipment.delivered_at = timezone.now()
            return_shipment.save(update_fields=["status", "delivered_at", "updated_at"])
    return return_request


def update_return_shipment_tracking(return_request, *, carrier, tracking_number="", tracking_url=""):
    """
    The buyer (or staff, if a pickup was arranged for them) self-reports
    carrier/tracking once the return parcel is actually on its way.
    Flips AWAITING_DISPATCH -> SHIPPED; re-submitting to correct details
    while already SHIPPED/IN_TRANSIT is allowed and doesn't reset the
    timestamp.
    """
    return_shipment = getattr(return_request, "return_shipment", None)
    if return_shipment is None:
        raise ValueError(f"Return {return_request.pk} isn't approved yet — nothing to ship.")
    if return_shipment.status in (ReturnShipment.Status.DELIVERED, ReturnShipment.Status.LOST):
        raise ValueError(f"This return shipment is already '{return_shipment.get_status_display()}'.")

    return_shipment.carrier = carrier
    return_shipment.tracking_number = tracking_number
    return_shipment.tracking_url = tracking_url
    update_fields = ["carrier", "tracking_number", "tracking_url", "updated_at"]
    if return_shipment.status == ReturnShipment.Status.AWAITING_DISPATCH:
        return_shipment.status = ReturnShipment.Status.SHIPPED
        return_shipment.shipped_at = timezone.now()
        update_fields += ["status", "shipped_at"]
    return_shipment.save(update_fields=update_fields)
    return return_shipment


def initiate_return_refund(return_request, amount, *, reason="", initiated_by=None):
    if return_request.status != ReturnRequest.Status.RECEIVED:
        raise ValueError(
            f"Return {return_request.pk} must be Received before a refund can be initiated "
            f"(currently '{return_request.status}')."
        )

    payment = return_request.refundable_payment
    if payment is None:
        raise ValueError("No refundable payment found for this order.")

    from payments.services import PaymentService

    refund = PaymentService().initiate_refund(
        payment,
        amount,
        reason=reason or f"Return {return_request.pk}",
        initiated_by=initiated_by,
        return_request=return_request,
    )
    return_request.status = ReturnRequest.Status.REFUND_INITIATED
    return_request.save(update_fields=["status", "updated_at"])
    return refund


def cancel_return_request(return_request, *, cancelled_by=None):
    if return_request.status not in ReturnRequest.CANCELLABLE_STATUSES:
        raise ValueError(f"Return {return_request.pk} can't be cancelled from status '{return_request.status}'.")
    return_request.status = ReturnRequest.Status.CANCELLED
    return_request.save(update_fields=["status", "updated_at"])
    return return_request


def mark_return_refunded(return_request):
    """Called from `payments.pipeline.on_refund_processed` once the refund actually clears."""
    return_request.status = ReturnRequest.Status.REFUNDED
    return_request.save(update_fields=["status", "updated_at"])
    return return_request


def mark_return_refund_failed(return_request):
    """
    Called from `payments.pipeline.on_refund_failed`. Drops back to RECEIVED
    (not REQUESTED/APPROVED) so staff can retry the refund from where it
    left off, without re-doing the physical receiving step.
    """
    return_request.status = ReturnRequest.Status.RECEIVED
    return_request.save(update_fields=["status", "updated_at"])
    return return_request
