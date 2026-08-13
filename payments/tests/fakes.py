"""
A scriptable in-memory stand-in for a real gateway, so `payments.services`
and `payments.pipeline` tests never touch the real Razorpay SDK or
network. Tests configure `next_*` attributes before calling into
`PaymentService`, then assert on what it did with the (fake) gateway's
response.
"""

import json
from decimal import Decimal

from payments.gateways.base import GatewayOrder, GatewayPayment, GatewayRefund, PaymentGateway, WebhookEventData
from payments.models import Payment


class FakeGateway(PaymentGateway):
    name = "razorpay"

    def __init__(self):
        self.orders_created = []
        self.refunds_initiated = []
        self.last_gateway_order_id = "order_fake_1"
        self.next_signature_valid = True
        self.next_payment_status = Payment.Status.CAPTURED
        self.next_payment_method = Payment.Method.UPI
        self.next_failure_reason = ""
        self.next_webhook_signature_valid = True
        self.next_refund_status = "processed"

    def create_order(self, *, amount, currency, receipt, notes):
        self.last_gateway_order_id = f"order_fake_{len(self.orders_created) + 1}"
        self.orders_created.append({"amount": amount, "currency": currency, "receipt": receipt, "notes": notes})
        return GatewayOrder(
            gateway_order_id=self.last_gateway_order_id,
            amount=amount,
            currency=currency,
            raw_response={"id": self.last_gateway_order_id, "amount": int(amount * 100), "currency": currency},
        )

    def verify_payment_signature(self, *, gateway_order_id, gateway_payment_id, signature):
        return self.next_signature_valid

    def fetch_payment(self, gateway_payment_id):
        return GatewayPayment(
            gateway_payment_id=gateway_payment_id or "pay_fake_1",
            gateway_order_id=self.last_gateway_order_id,
            status=self.next_payment_status,
            method=self.next_payment_method,
            amount=Decimal("0.00"),
            currency="INR",
            captured=self.next_payment_status == Payment.Status.CAPTURED,
            transaction_reference="RRN123456",
            failure_reason=self.next_failure_reason,
            raw_response={"id": gateway_payment_id, "status": self.next_payment_status},
        )

    def capture_payment(self, gateway_payment_id, amount):
        return self.fetch_payment(gateway_payment_id)

    def initiate_refund(self, gateway_payment_id, amount, *, notes):
        gateway_refund_id = f"rfnd_fake_{len(self.refunds_initiated) + 1}"
        self.refunds_initiated.append({"gateway_payment_id": gateway_payment_id, "amount": amount, "notes": notes})
        return GatewayRefund(
            gateway_refund_id=gateway_refund_id,
            amount=amount,
            status=self.next_refund_status,
            raw_response={"id": gateway_refund_id, "status": self.next_refund_status},
        )

    def verify_webhook_signature(self, *, raw_body, signature):
        return self.next_webhook_signature_valid

    def parse_webhook_event(self, *, raw_body, headers):
        payload = json.loads(raw_body)
        entities = payload.get("payload", {})
        payment_entity = entities.get("payment", {}).get("entity", {})
        refund_entity = entities.get("refund", {}).get("entity", {})
        order_entity = entities.get("order", {}).get("entity", {})
        dedupe_key = headers.get("X-Razorpay-Event-Id") or (
            f"{payload.get('event', '')}:{payload.get('created_at', '')}"
        )
        return WebhookEventData(
            dedupe_key=dedupe_key,
            event_type=payload.get("event", ""),
            gateway_payment_id=payment_entity.get("id", "") or refund_entity.get("payment_id", ""),
            gateway_order_id=payment_entity.get("order_id", "") or order_entity.get("id", ""),
            payload=payload,
        )


def razorpay_webhook_payload(event_type, *, payment_id="pay_fake_1", order_id="order_fake_1", refund_id=None):
    """Builds a minimal, structurally-correct Razorpay webhook envelope for the given event type."""
    payload = {"payment": {"entity": {"id": payment_id, "order_id": order_id}}}
    if refund_id:
        payload["refund"] = {"entity": {"id": refund_id, "payment_id": payment_id}}
    if event_type == "order.paid":
        payload["order"] = {"entity": {"id": order_id}}
    return json.dumps({"event": event_type, "created_at": 1700000000, "payload": payload}).encode("utf-8")
