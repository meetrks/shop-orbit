"""
Unit tests for `RazorpayGateway`'s wire-format handling — paise/rupee
conversion, status/method normalization, and webhook payload parsing.
None of these touch the network: `razorpay.Client.__init__` doesn't make
any API calls, and every method under test here doesn't call the client.
"""

from decimal import Decimal

from django.test import TestCase

from payments.gateways.razorpay_gateway import RazorpayGateway
from payments.models import Payment


class RazorpayGatewayUnitTests(TestCase):
    def setUp(self):
        self.gateway = RazorpayGateway()

    def test_to_paise_converts_rupees_to_integer_paise(self):
        self.assertEqual(self.gateway._to_paise(Decimal("799.00")), 79900)
        self.assertEqual(self.gateway._to_paise(Decimal("10.50")), 1050)

    def test_to_rupees_converts_paise_back_to_decimal(self):
        self.assertEqual(self.gateway._to_rupees(79900), Decimal("799.00"))
        self.assertEqual(self.gateway._to_rupees(1050), Decimal("10.50"))

    def test_normalize_payment_maps_known_status_and_method(self):
        response = {
            "id": "pay_123",
            "order_id": "order_123",
            "status": "captured",
            "method": "upi",
            "amount": 79900,
            "currency": "INR",
            "captured": True,
            "acquirer_data": {"upi_transaction_id": "UTR123"},
        }
        result = self.gateway._normalize_payment(response)
        self.assertEqual(result.status, Payment.Status.CAPTURED)
        self.assertEqual(result.method, Payment.Method.UPI)
        self.assertEqual(result.amount, Decimal("799.00"))
        self.assertTrue(result.captured)
        self.assertEqual(result.transaction_reference, "UTR123")

    def test_normalize_payment_falls_back_to_other_for_unknown_method(self):
        response = {
            "id": "pay_123",
            "order_id": "order_123",
            "status": "captured",
            "method": "cardless_emi",
            "amount": 100,
            "currency": "INR",
            "captured": True,
        }
        result = self.gateway._normalize_payment(response)
        self.assertEqual(result.method, Payment.Method.OTHER)

    def test_normalize_payment_captures_failure_reason(self):
        response = {
            "id": "pay_123",
            "order_id": "order_123",
            "status": "failed",
            "method": "card",
            "amount": 100,
            "currency": "INR",
            "captured": False,
            "error_description": "Insufficient funds.",
        }
        result = self.gateway._normalize_payment(response)
        self.assertEqual(result.status, Payment.Status.FAILED)
        self.assertEqual(result.failure_reason, "Insufficient funds.")

    def test_parse_webhook_event_extracts_payment_captured(self):
        raw_body = (
            b'{"event": "payment.captured", "created_at": 123, "payload": '
            b'{"payment": {"entity": {"id": "pay_1", "order_id": "order_1"}}}}'
        )
        event = self.gateway.parse_webhook_event(raw_body=raw_body, headers={"X-Razorpay-Event-Id": "evt_1"})
        self.assertEqual(event.event_type, "payment.captured")
        self.assertEqual(event.gateway_payment_id, "pay_1")
        self.assertEqual(event.gateway_order_id, "order_1")
        self.assertEqual(event.dedupe_key, "evt_1")

    def test_parse_webhook_event_extracts_refund_payment_id(self):
        raw_body = (
            b'{"event": "refund.processed", "created_at": 123, "payload": '
            b'{"payment": {"entity": {"id": "pay_1", "order_id": "order_1"}}, '
            b'"refund": {"entity": {"id": "rfnd_1", "payment_id": "pay_1"}}}}'
        )
        event = self.gateway.parse_webhook_event(raw_body=raw_body, headers={})
        self.assertEqual(event.gateway_payment_id, "pay_1")

    def test_parse_webhook_event_falls_back_to_hashed_dedupe_key_without_header(self):
        raw_body = (
            b'{"event": "payment.failed", "created_at": 999, "payload": '
            b'{"payment": {"entity": {"id": "pay_2", "order_id": "order_2"}}}}'
        )
        event_a = self.gateway.parse_webhook_event(raw_body=raw_body, headers={})
        event_b = self.gateway.parse_webhook_event(raw_body=raw_body, headers={})
        # Same payload delivered twice (a real gateway retry) must hash to
        # the same dedupe key, or duplicate-delivery detection can't work.
        self.assertEqual(event_a.dedupe_key, event_b.dedupe_key)
        self.assertTrue(event_a.dedupe_key)
