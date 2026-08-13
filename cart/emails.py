"""
Order lifecycle notification emails, sent via common.emails.

The buyer-facing "your order is confirmed" email now lives in
`payments.emails.send_payment_succeeded_email` — it's only sent once
payment actually captures, since a customer shouldn't get an order
confirmation for something they haven't paid for yet (and this storefront
no longer creates orders that might just sit unpaid, per the Razorpay
checkout flow in `cart.views.checkout`). The site operator is notified
from here, called from `payments.pipeline.on_payment_captured` alongside
that email.
"""

from django.conf import settings
from django.urls import reverse

from common.emails import send_templated_email


def _absolute_url(path):
    """Templates render outside any request, so a relative `reverse()` path needs a scheme+domain stitched on."""
    return f"{settings.SITE_SCHEME}://{settings.SITE_DOMAIN}{path}"


def send_new_order_staff_notification(order):
    """Emails the operator (STAFF_NOTIFICATION_EMAIL) once an order's payment has captured."""
    admin_url = _absolute_url(reverse("admin:cart_order_change", args=[order.pk]))
    send_templated_email(
        "new_order_notification",
        {"order": order, "items": order.items.all(), "admin_url": admin_url},
        to=settings.STAFF_NOTIFICATION_EMAIL,
    )


def send_order_status_changed_email(order, previous_status):
    send_templated_email(
        "order_status_changed",
        {
            "order": order,
            "previous_status_display": dict(order.Status.choices).get(previous_status, previous_status),
        },
        to=order.user.email,
    )
