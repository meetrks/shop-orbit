"""
Gateway abstraction layer.

`payments.services.PaymentService` is the only caller allowed to depend on
`PaymentGateway`; nothing in `cart`, `fulfillment`, the admin, or templates
should ever import a gateway SDK directly. Adding a new provider (Cashfree,
PhonePe, PayU, ...) means writing one subclass of `PaymentGateway` and
adding it to `GATEWAY_CLASSES` below — nothing else in the codebase changes.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import GatewayOrder, GatewayPayment, GatewayRefund, PaymentGateway, WebhookEventData
from .razorpay_gateway import RazorpayGateway

GATEWAY_CLASSES = {
    "razorpay": RazorpayGateway,
}

_instances = {}


def get_gateway(name=None):
    """Returns a cached `PaymentGateway` instance for `name` (default: `settings.DEFAULT_PAYMENT_GATEWAY`)."""
    name = name or settings.DEFAULT_PAYMENT_GATEWAY
    if name not in _instances:
        try:
            gateway_class = GATEWAY_CLASSES[name]
        except KeyError:
            raise ImproperlyConfigured(f"No payment gateway registered under the name '{name}'.")
        _instances[name] = gateway_class()
    return _instances[name]


__all__ = [
    "GatewayOrder",
    "GatewayPayment",
    "GatewayRefund",
    "PaymentGateway",
    "WebhookEventData",
    "get_gateway",
]
