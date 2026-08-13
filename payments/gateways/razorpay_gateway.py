"""
Razorpay implementation of `PaymentGateway`.

This is the only module in the codebase that imports the `razorpay` SDK or
knows anything about Razorpay's wire format (paise amounts, its status
vocabulary, its webhook envelope). Everything upstream of this class
(`payments.services.PaymentService` and beyond) only ever sees the
normalized `GatewayOrder` / `GatewayPayment` / `GatewayRefund` /
`WebhookEventData` shapes from `payments.gateways.base`.
"""

import hashlib
import json
from decimal import Decimal

import razorpay
from django.conf import settings
from razorpay.errors import SignatureVerificationError

from payments.models import Payment

from .base import GatewayOrder, GatewayPayment, GatewayRefund, PaymentGateway, WebhookEventData

# Razorpay's payment.status vocabulary -> our normalized Payment.Status.
_STATUS_MAP = {
    "created": Payment.Status.CREATED,
    "authorized": Payment.Status.AUTHORIZED,
    "captured": Payment.Status.CAPTURED,
    "failed": Payment.Status.FAILED,
    "refunded": Payment.Status.REFUNDED,
}

# Razorpay's payment.method vocabulary -> our normalized Payment.Method.
# Anything not listed here (paylater, cardless_emi, ...) maps to OTHER
# rather than raising, since Razorpay adds new methods over time and a
# payment that succeeded shouldn't fail to record just because of that.
_METHOD_MAP = {
    "upi": Payment.Method.UPI,
    "card": Payment.Method.CARD,
    "netbanking": Payment.Method.NETBANKING,
    "wallet": Payment.Method.WALLET,
    "emi": Payment.Method.EMI,
}


class RazorpayGateway(PaymentGateway):
    name = "razorpay"

    def __init__(self):
        self._client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    @staticmethod
    def _to_paise(amount: Decimal) -> int:
        return int((amount * 100).to_integral_value())

    @staticmethod
    def _to_rupees(paise) -> Decimal:
        return (Decimal(paise) / Decimal("100")).quantize(Decimal("0.01"))

    def create_order(self, *, amount: Decimal, currency: str, receipt: str, notes: dict) -> GatewayOrder:
        response = self._client.order.create(
            data={
                "amount": self._to_paise(amount),
                "currency": currency,
                "receipt": receipt,
                "notes": notes,
                # Auto-capture: we already validate stock/pricing/coupons
                # server-side before creating the order, so there's no
                # separate manual-review step that needs the funds left
                # merely authorized.
                "payment_capture": 1,
            }
        )
        return GatewayOrder(
            gateway_order_id=response["id"],
            amount=amount,
            currency=currency,
            raw_response=response,
        )

    def verify_payment_signature(self, *, gateway_order_id: str, gateway_payment_id: str, signature: str) -> bool:
        try:
            self._client.utility.verify_payment_signature(
                {
                    "razorpay_order_id": gateway_order_id,
                    "razorpay_payment_id": gateway_payment_id,
                    "razorpay_signature": signature,
                }
            )
        except SignatureVerificationError:
            return False
        return True

    def fetch_payment(self, gateway_payment_id: str) -> GatewayPayment:
        response = self._client.payment.fetch(gateway_payment_id)
        return self._normalize_payment(response)

    def capture_payment(self, gateway_payment_id: str, amount: Decimal) -> GatewayPayment:
        response = self._client.payment.capture(gateway_payment_id, self._to_paise(amount), {"currency": "INR"})
        return self._normalize_payment(response)

    def initiate_refund(self, gateway_payment_id: str, amount: Decimal, *, notes: dict) -> GatewayRefund:
        response = self._client.payment.refund(gateway_payment_id, {"amount": self._to_paise(amount), "notes": notes})
        # Razorpay reports a freshly-created refund as "pending" or already
        # "processed"; it can later move to "processed" or "failed" via a
        # refund.processed / refund.failed webhook. `payments.services`
        # treats both "pending" and "processed" as Refund.Status.INITIATED
        # until a webhook confirms the terminal outcome.
        return GatewayRefund(
            gateway_refund_id=response["id"],
            amount=self._to_rupees(response["amount"]),
            status=response.get("status", "pending"),
            raw_response=response,
        )

    def verify_webhook_signature(self, *, raw_body: bytes, signature: str) -> bool:
        try:
            return bool(
                self._client.utility.verify_webhook_signature(
                    raw_body.decode("utf-8"), signature, settings.RAZORPAY_WEBHOOK_SECRET
                )
            )
        except SignatureVerificationError:
            return False

    def parse_webhook_event(self, *, raw_body: bytes, headers: dict) -> WebhookEventData:
        payload = json.loads(raw_body)
        event_type = payload.get("event", "")
        entities = payload.get("payload", {})

        # Every event type Razorpay sends us includes the payment entity
        # (directly for payment.*, alongside the refund entity for
        # refund.*, and alongside the order entity for order.paid), so
        # pulling gateway_payment_id/gateway_order_id from it works
        # uniformly regardless of which entity type the event is really
        # about.
        payment_entity = entities.get("payment", {}).get("entity", {})
        order_entity = entities.get("order", {}).get("entity", {})
        refund_entity = entities.get("refund", {}).get("entity", {})

        gateway_payment_id = payment_entity.get("id", "") or refund_entity.get("payment_id", "")
        gateway_order_id = payment_entity.get("order_id", "") or order_entity.get("id", "")

        dedupe_key = (
            headers.get("X-Razorpay-Event-Id")
            or headers.get("x-razorpay-event-id")
            or self._fallback_dedupe_key(payload)
        )

        return WebhookEventData(
            dedupe_key=dedupe_key,
            event_type=event_type,
            gateway_payment_id=gateway_payment_id,
            gateway_order_id=gateway_order_id,
            payload=payload,
        )

    @staticmethod
    def _fallback_dedupe_key(payload: dict) -> str:
        """
        Razorpay has sent `X-Razorpay-Event-Id` on webhook deliveries since
        2022, but we don't control the merchant's dashboard configuration
        or SDK version, so this is a defensive fallback, not the primary
        path: a stable hash of the event type + entity id + created_at
        still de-duplicates retried deliveries of the *same* event even if
        the header is ever absent.
        """
        digest_source = json.dumps(
            {
                "event": payload.get("event"),
                "created_at": payload.get("created_at"),
                "payload": payload.get("payload"),
            },
            sort_keys=True,
        )
        return hashlib.sha256(digest_source.encode("utf-8")).hexdigest()

    def _normalize_payment(self, response: dict) -> GatewayPayment:
        status = _STATUS_MAP.get(response.get("status"), Payment.Status.PENDING)
        method = _METHOD_MAP.get(response.get("method"), Payment.Method.OTHER)
        return GatewayPayment(
            gateway_payment_id=response["id"],
            gateway_order_id=response.get("order_id", ""),
            status=status,
            method=method,
            amount=self._to_rupees(response["amount"]),
            currency=response.get("currency", "INR"),
            captured=bool(response.get("captured", False)),
            transaction_reference=response.get("acquirer_data", {}).get("rrn")
            or response.get("acquirer_data", {}).get("upi_transaction_id", "")
            or "",
            failure_reason=response.get("error_description") or "",
            raw_response=response,
        )
