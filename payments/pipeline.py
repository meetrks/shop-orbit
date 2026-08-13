"""
What happens after a payment definitively succeeds or fails, and after a
refund is confirmed — called only from `payments.services` once a gateway
event (client-side verification or webhook) has been applied to a
`Payment`. Every entry point here is idempotent: it's always safe to call
again for the same payment/refund, because a webhook and a client-side
verification can legitimately race to report the same event, and either
one might fire the pipeline first.
"""

from django.db import transaction

from cart.emails import send_new_order_staff_notification
from cart.models import Order
from fulfillment.services import create_shipment, generate_invoice, generate_packing_slip
from inventory.services import convert_reservation_to_sale, release_reservation

from .emails import (
    send_payment_failed_email,
    send_payment_succeeded_email,
    send_refund_failed_email,
    send_refund_processed_email,
)
from .models import Payment


def on_payment_captured(payment):
    """
    Confirms the order, reduces stock, and generates the GST invoice,
    packing slip, and shipment record. Guarded by `Order.status`, so a
    second call for the same order — the webhook arriving after
    client-side verification already ran this, or vice versa — is a no-op.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        if order.status != Order.Status.AWAITING_PAYMENT:
            return
        convert_reservation_to_sale(order)
        order.status = Order.Status.PAYMENT_CONFIRMED
        _save_status_without_generic_email(order)

    invoice = generate_invoice(order)
    generate_packing_slip(order)
    create_shipment(order)
    send_payment_succeeded_email(order, payment, invoice)
    send_new_order_staff_notification(order)


def on_payment_failed(payment):
    """
    Releases the stock this order's checkout was holding and marks the
    order payment-failed so the buyer can retry from their order page —
    a retry (see `cart.views.payment_retry`) re-reserves stock fresh
    rather than assuming it's still held. Idempotent, same guard as above.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        if order.status != Order.Status.AWAITING_PAYMENT:
            return
        release_reservation(order)
        order.status = Order.Status.PAYMENT_FAILED
        _save_status_without_generic_email(order)

    send_payment_failed_email(order, payment)


def on_refund_processed(refund):
    payment = refund.payment
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        order.status = (
            Order.Status.REFUNDED
            if payment.refund_status == Payment.RefundStatus.FULL
            else Order.Status.PARTIALLY_REFUNDED
        )
        _save_status_without_generic_email(order)

    if refund.return_request_id:
        from returns.services import mark_return_refunded

        mark_return_refunded(refund.return_request)

    send_refund_processed_email(order, payment, refund)


def on_refund_failed(refund):
    if refund.return_request_id:
        from returns.services import mark_return_refund_failed

        mark_return_refund_failed(refund.return_request)

    send_refund_failed_email(refund.payment.order, refund.payment, refund)


def _save_status_without_generic_email(order):
    """
    Every status transition triggered from here has its own dedicated,
    richer email (payment succeeded/failed, refund processed) sent right
    after — unlike a plain staff-driven status edit in the admin, which
    only has `cart.signals`' generic "your order is now X" email to fall
    back on. Without this flag, the buyer would get both for the same
    transition.
    """
    order._skip_status_email = True
    order.save(update_fields=["status", "updated_at"])
