"""
The gateway-agnostic contract every payment provider integration must
implement. Amounts crossing this boundary are always `Decimal` rupees on
our side; each gateway implementation is responsible for converting to/from
whatever unit its own API expects (Razorpay: integer paise).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class GatewayOrder:
    """Normalized result of creating an order with the gateway."""

    gateway_order_id: str
    amount: Decimal
    currency: str
    raw_response: dict = field(default_factory=dict)


@dataclass
class GatewayPayment:
    """
    Normalized snapshot of a payment's state as the gateway sees it right
    now. `status`/`method` are already mapped to `payments.models.Payment`
    choices by the gateway implementation, so callers never branch on
    provider-specific strings.
    """

    gateway_payment_id: str
    gateway_order_id: str
    status: str
    method: str
    amount: Decimal
    currency: str
    captured: bool
    transaction_reference: str = ""
    failure_reason: str = ""
    raw_response: dict = field(default_factory=dict)


@dataclass
class GatewayRefund:
    """Normalized result of initiating (or checking) a refund with the gateway."""

    gateway_refund_id: str
    amount: Decimal
    status: str
    raw_response: dict = field(default_factory=dict)


@dataclass
class WebhookEventData:
    """A verified webhook delivery, normalized to a provider-independent shape."""

    dedupe_key: str
    event_type: str
    gateway_payment_id: str
    gateway_order_id: str
    payload: dict = field(default_factory=dict)


class PaymentGateway(ABC):
    """Base interface every payment gateway integration must implement."""

    #: Short slug matching a `payments.models.Payment.Gateway` value.
    name: str

    @abstractmethod
    def create_order(self, *, amount: Decimal, currency: str, receipt: str, notes: dict) -> GatewayOrder:
        """Creates an order with the gateway ahead of showing the checkout UI."""

    @abstractmethod
    def verify_payment_signature(self, *, gateway_order_id: str, gateway_payment_id: str, signature: str) -> bool:
        """Verifies the signature the gateway's checkout script hands back to the browser."""

    @abstractmethod
    def fetch_payment(self, gateway_payment_id: str) -> GatewayPayment:
        """Fetches a payment's authoritative current state directly from the gateway (never trust the client)."""

    @abstractmethod
    def capture_payment(self, gateway_payment_id: str, amount: Decimal) -> GatewayPayment:
        """Explicitly captures an authorized-but-not-yet-captured payment."""

    @abstractmethod
    def initiate_refund(self, gateway_payment_id: str, amount: Decimal, *, notes: dict) -> GatewayRefund:
        """Initiates a full or partial refund against a captured payment."""

    @abstractmethod
    def verify_webhook_signature(self, *, raw_body: bytes, signature: str) -> bool:
        """Verifies a webhook delivery's signature against the configured webhook secret."""

    @abstractmethod
    def parse_webhook_event(self, *, raw_body: bytes, headers: dict) -> WebhookEventData:
        """Parses an already-signature-verified webhook delivery into a normalized `WebhookEventData`."""
