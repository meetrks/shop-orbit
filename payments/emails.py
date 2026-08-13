"""Payment and refund lifecycle notification emails, sent via common.emails."""

from django.conf import settings
from django.urls import reverse

from common.emails import send_templated_email


def _absolute_url(path):
    """Templates render outside any request, so a relative `reverse()` path needs a scheme+domain stitched on."""
    return f"{settings.SITE_SCHEME}://{settings.SITE_DOMAIN}{path}"


def send_payment_succeeded_email(order, payment, invoice):
    """The buyer's order-confirmation email — sent once, right after payment capture."""
    invoice_url = _absolute_url(reverse("fulfillment:invoice_download", args=[order.order_number]))
    send_templated_email(
        "payment_succeeded",
        {"order": order, "payment": payment, "invoice": invoice, "invoice_url": invoice_url},
        to=order.user.email,
    )


def send_payment_failed_email(order, payment):
    retry_url = _absolute_url(reverse("cart:checkout_payment", args=[order.order_number]))
    send_templated_email(
        "payment_failed", {"order": order, "payment": payment, "retry_url": retry_url}, to=order.user.email
    )


def send_refund_processed_email(order, payment, refund):
    send_templated_email(
        "refund_processed",
        {
            "order": order,
            "payment": payment,
            "refund": refund,
            "remaining_amount": payment.amount - payment.refunded_amount,
        },
        to=order.user.email,
    )


def send_refund_failed_email(order, payment, refund):
    # cc'd to staff since the copy tells the buyer "our team has been
    # notified and will follow up" — a failed refund needs a human to
    # actually retry it from the gateway dashboard or admin, so someone
    # needs to see this land.
    send_templated_email(
        "refund_failed",
        {"order": order, "payment": payment, "refund": refund},
        to=order.user.email,
        cc=settings.STAFF_NOTIFICATION_EMAIL,
    )
