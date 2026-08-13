"""
Tests for `payments.pipeline` — what happens after a payment/refund is
definitively resolved. Calls the pipeline functions directly (rather than
through a webhook/client-callback) since they're the unit under test here;
`test_services.py` covers that they're wired up correctly from there.
"""

from decimal import Decimal

from django.conf import settings
from django.core import mail
from django.test import TestCase

from accounts.models import User
from cart.models import Order, OrderItem
from catalog.models import Category, Department, Product, Subcategory
from fulfillment.models import Invoice, PackingSlip, Shipment
from inventory.services import reserve_stock
from payments import pipeline
from payments.models import Payment, Refund


class PipelineTestCase(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        subcategory = Subcategory.objects.create(category=category, name="Sarees")
        self.product = Product.objects.create(
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
        self.order_item = OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_title=self.product.title,
            product_sku=self.product.sku,
            unit_price=Decimal("999.00"),
            quantity=2,
            hsn_code="6204",
            gst_rate=Decimal("5.00"),
        )
        # Matches the real flow: checkout reserves stock before payment is
        # ever attempted (see cart.views.checkout).
        reserve_stock(self.order)
        self.payment = Payment.objects.create(
            order=self.order,
            user=self.buyer,
            gateway_order_id="order_1",
            gateway_payment_id="pay_1",
            amount=Decimal("999.00"),
            status=Payment.Status.CAPTURED,
        )

    def test_on_payment_captured_confirms_order_and_reduces_stock(self):
        pipeline.on_payment_captured(self.payment)

        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAYMENT_CONFIRMED)
        self.assertEqual(self.product.stock_count, 8)  # 10 - 2
        self.assertEqual(self.product.reserved_count, 0)  # reservation converted, not just left dangling

    def test_on_payment_captured_generates_fulfillment_records(self):
        pipeline.on_payment_captured(self.payment)

        self.assertTrue(Invoice.objects.filter(order=self.order).exists())
        self.assertTrue(PackingSlip.objects.filter(order=self.order).exists())
        self.assertTrue(Shipment.objects.filter(order=self.order).exists())

    def test_on_payment_captured_sends_exactly_one_email_per_recipient(self):
        pipeline.on_payment_captured(self.payment)

        # Buyer's payment-succeeded email + the operator's new-order
        # notification — and *not* a duplicate generic "order status
        # changed" email for the same transition (see cart.signals'
        # _skip_status_email guard).
        self.assertEqual(len(mail.outbox), 2)
        recipients = {addr for message in mail.outbox for addr in message.to}
        self.assertEqual(recipients, {"buyer@example.com", settings.STAFF_NOTIFICATION_EMAIL})
        subjects = [message.subject for message in mail.outbox]
        self.assertTrue(any("confirmed" in s.lower() or "received" in s.lower() for s in subjects))

    def test_on_payment_captured_is_idempotent(self):
        pipeline.on_payment_captured(self.payment)
        self.product.refresh_from_db()
        stock_after_first = self.product.stock_count

        # Simulates the webhook arriving after client-side verification
        # already ran the pipeline for this same payment.
        pipeline.on_payment_captured(self.payment)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_count, stock_after_first)
        self.assertEqual(Invoice.objects.filter(order=self.order).count(), 1)
        # Only the first call's emails — the second call returns early
        # because the order is no longer AWAITING_PAYMENT.
        self.assertEqual(len(mail.outbox), 2)

    def test_on_payment_captured_floors_stock_at_zero_when_oversold(self):
        self.order_item.quantity = 999
        self.order_item.save()

        pipeline.on_payment_captured(self.payment)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_count, 0)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAYMENT_CONFIRMED)

    def test_on_payment_failed_marks_order_releases_reservation_and_sends_one_email(self):
        self.order.status = Order.Status.AWAITING_PAYMENT
        self.order.save()
        self.payment.status = Payment.Status.FAILED
        self.payment.failure_reason = "Card declined."
        self.payment.save()

        pipeline.on_payment_failed(self.payment)

        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAYMENT_FAILED)
        self.assertEqual(self.product.reserved_count, 0)  # the setUp reservation was released
        self.assertEqual(self.product.stock_count, 10)  # never actually decremented
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("buyer@example.com", mail.outbox[0].to)

    def test_on_refund_processed_marks_order_fully_refunded(self):
        self.order.status = Order.Status.PAYMENT_CONFIRMED
        self.order.save()
        self.payment.refunded_amount = self.payment.amount
        self.payment.refund_status = Payment.RefundStatus.FULL
        self.payment.save()
        refund = Refund.objects.create(
            payment=self.payment, amount=self.payment.amount, status=Refund.Status.PROCESSED
        )

        pipeline.on_refund_processed(refund)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.REFUNDED)
        self.assertEqual(len(mail.outbox), 1)

    def test_on_refund_processed_marks_order_partially_refunded(self):
        self.order.status = Order.Status.PAYMENT_CONFIRMED
        self.order.save()
        self.payment.refunded_amount = Decimal("500.00")
        self.payment.refund_status = Payment.RefundStatus.PARTIAL
        self.payment.save()
        refund = Refund.objects.create(payment=self.payment, amount=Decimal("500.00"), status=Refund.Status.PROCESSED)

        pipeline.on_refund_processed(refund)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PARTIALLY_REFUNDED)
