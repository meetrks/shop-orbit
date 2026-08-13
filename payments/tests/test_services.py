"""
Tests for `PaymentService`, the only entry point the rest of the codebase
uses to move money. Uses `FakeGateway` (see fakes.py) instead of the real
Razorpay SDK, so these never touch the network — patched in via
`payments.services.get_gateway`, exactly where `PaymentService.__init__`
looks it up.
"""

from decimal import Decimal
from unittest import mock

from django.test import TestCase

from accounts.models import User
from cart.models import Order, OrderItem
from catalog.models import Category, Department, Product, Subcategory
from payments.models import Payment, PaymentEvent, Refund, WebhookEvent
from payments.services import PaymentService

from .fakes import FakeGateway, razorpay_webhook_payload


class PaymentServiceTestCase(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        subcategory = Subcategory.objects.create(category=category, name="Sarees")
        product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
            stock_count=10,
        )
        self.order = Order.objects.create(
            user=self.buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="123 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00"),
        )
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_title=product.title,
            product_sku=product.sku,
            unit_price=Decimal("999.00"),
            quantity=1,
            hsn_code="6204",
            gst_rate=Decimal("5.00"),
        )

        self.fake_gateway = FakeGateway()
        patcher = mock.patch("payments.services.get_gateway", return_value=self.fake_gateway)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service = PaymentService()

    def test_create_payment_for_order_creates_payment_and_event(self):
        payment = self.service.create_payment_for_order(self.order, self.buyer)
        self.assertEqual(payment.status, Payment.Status.CREATED)
        self.assertEqual(payment.amount, self.order.total_amount)
        self.assertEqual(payment.gateway_order_id, self.fake_gateway.last_gateway_order_id)
        self.assertEqual(PaymentEvent.objects.filter(payment=payment, event_type="created").count(), 1)

    def test_verify_client_callback_captures_on_valid_signature(self):
        payment = self.service.create_payment_for_order(self.order, self.buyer)
        self.fake_gateway.next_payment_status = Payment.Status.CAPTURED

        # The post-capture pipeline (stock reduction, invoice/order status)
        # runs from a transaction.on_commit hook — TestCase wraps each test
        # in a transaction that's rolled back rather than committed, so
        # on_commit callbacks are silently dropped unless captured like this.
        with self.captureOnCommitCallbacks(execute=True):
            captured = self.service.verify_client_callback(
                payment,
                gateway_payment_id="pay_1",
                gateway_order_id=payment.gateway_order_id,
                signature="sig_ok",
            )

        self.assertTrue(captured)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CAPTURED)
        self.assertEqual(payment.gateway_payment_id, "pay_1")
        self.assertIsNotNone(payment.captured_at)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAYMENT_CONFIRMED)

    def test_verify_client_callback_does_not_touch_status_on_bad_signature(self):
        payment = self.service.create_payment_for_order(self.order, self.buyer)
        self.fake_gateway.next_signature_valid = False

        captured = self.service.verify_client_callback(
            payment,
            gateway_payment_id="pay_1",
            gateway_order_id=payment.gateway_order_id,
            signature="sig_bad",
        )

        self.assertFalse(captured)
        payment.refresh_from_db()
        # A forged/garbled callback proves nothing — status must be left
        # exactly as it was, not flipped to FAILED.
        self.assertEqual(payment.status, Payment.Status.CREATED)

    def test_verify_client_callback_rejects_order_id_mismatch(self):
        payment = self.service.create_payment_for_order(self.order, self.buyer)
        captured = self.service.verify_client_callback(
            payment, gateway_payment_id="pay_1", gateway_order_id="some_other_order", signature="sig_ok"
        )
        self.assertFalse(captured)

    def test_process_webhook_applies_payment_captured_event(self):
        payment = self.service.create_payment_for_order(self.order, self.buyer)
        payment.gateway_order_id = "order_fake_1"
        payment.save()
        self.fake_gateway.last_gateway_order_id = "order_fake_1"
        self.fake_gateway.next_payment_status = Payment.Status.CAPTURED

        body = razorpay_webhook_payload("payment.captured", payment_id="pay_1", order_id="order_fake_1")
        with self.captureOnCommitCallbacks(execute=True):
            handled = self.service.process_webhook(raw_body=body, headers={"X-Razorpay-Event-Id": "evt_1"})

        self.assertTrue(handled)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CAPTURED)
        self.assertEqual(WebhookEvent.objects.count(), 1)

    def test_process_webhook_is_idempotent_for_duplicate_delivery(self):
        payment = self.service.create_payment_for_order(self.order, self.buyer)
        payment.gateway_order_id = "order_fake_1"
        payment.save()
        self.fake_gateway.last_gateway_order_id = "order_fake_1"
        self.fake_gateway.next_payment_status = Payment.Status.CAPTURED

        body = razorpay_webhook_payload("payment.captured", payment_id="pay_1", order_id="order_fake_1")
        with self.captureOnCommitCallbacks(execute=True):
            self.service.process_webhook(raw_body=body, headers={"X-Razorpay-Event-Id": "evt_dup"})
        events_after_first = PaymentEvent.objects.filter(payment=payment).count()

        # A true duplicate delivery of the same event id.
        handled_again = self.service.process_webhook(raw_body=body, headers={"X-Razorpay-Event-Id": "evt_dup"})

        self.assertTrue(handled_again)
        self.assertEqual(WebhookEvent.objects.count(), 1)
        self.assertEqual(PaymentEvent.objects.filter(payment=payment).count(), events_after_first)

    def test_process_webhook_rejects_invalid_signature(self):
        self.fake_gateway.next_webhook_signature_valid = False
        body = razorpay_webhook_payload("payment.captured")
        handled = self.service.process_webhook(raw_body=body, headers={})
        self.assertFalse(handled)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    def test_initiate_refund_creates_refund_and_updates_payment(self):
        payment = self.service.create_payment_for_order(self.order, self.buyer)
        payment.status = Payment.Status.CAPTURED
        payment.gateway_payment_id = "pay_1"
        payment.save()
        self.fake_gateway.next_refund_status = "processed"

        refund = self.service.initiate_refund(payment, Decimal("999.00"), reason="Customer requested")

        self.assertEqual(refund.status, Refund.Status.PROCESSED)
        payment.refresh_from_db()
        self.assertEqual(payment.refunded_amount, Decimal("999.00"))
        self.assertEqual(payment.refund_status, Payment.RefundStatus.FULL)
        self.assertEqual(payment.status, Payment.Status.REFUNDED)

    def test_initiate_refund_rejects_amount_over_remaining_balance(self):
        payment = self.service.create_payment_for_order(self.order, self.buyer)
        payment.status = Payment.Status.CAPTURED
        payment.gateway_payment_id = "pay_1"
        payment.save()

        with self.assertRaises(ValueError):
            self.service.initiate_refund(payment, Decimal("5000.00"))

    def test_initiate_refund_partial_leaves_payment_partially_refunded(self):
        payment = self.service.create_payment_for_order(self.order, self.buyer)
        payment.status = Payment.Status.CAPTURED
        payment.gateway_payment_id = "pay_1"
        payment.save()

        self.service.initiate_refund(payment, Decimal("500.00"))

        payment.refresh_from_db()
        self.assertEqual(payment.refund_status, Payment.RefundStatus.PARTIAL)
        self.assertEqual(payment.status, Payment.Status.PARTIALLY_REFUNDED)
