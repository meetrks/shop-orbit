"""Tests for the return-request workflow in returns.services."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from cart.models import Order, OrderItem
from catalog.models import Category, Department, Product, Subcategory
from fulfillment.models import Shipment
from inventory.models import StockMovement
from payments import pipeline
from payments.models import Payment
from payments.services import PaymentService
from payments.tests.fakes import FakeGateway
from returns.models import ReturnRequest, ReturnShipment
from returns.services import (
    approve_return,
    cancel_return_request,
    initiate_return_refund,
    is_order_returnable,
    mark_return_received,
    reject_return,
    remaining_returnable_quantity,
    request_return,
    update_return_shipment_tracking,
)


class ReturnsServiceTestCase(TestCase):
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
            status=Order.Status.DELIVERED,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="123 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("2997.00"),
        )
        self.item = OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_title=self.product.title,
            product_sku=self.product.sku,
            unit_price=Decimal("999.00"),
            quantity=3,
            hsn_code="6204",
            gst_rate=Decimal("5.00"),
        )
        self.shipment = Shipment.objects.create(
            order=self.order, status=Shipment.Status.DELIVERED, delivered_at=timezone.now()
        )
        self.payment = Payment.objects.create(
            order=self.order,
            user=self.buyer,
            gateway_order_id="order_1",
            gateway_payment_id="pay_1",
            amount=Decimal("2997.00"),
            status=Payment.Status.CAPTURED,
        )

    def test_request_return_creates_request_and_lines_and_sends_emails(self):
        return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 2}],
            reason=ReturnRequest.Reason.DEFECTIVE,
            comments="Torn on arrival",
            requested_by=self.buyer,
        )

        self.assertEqual(return_request.status, ReturnRequest.Status.REQUESTED)
        self.assertEqual(return_request.lines.count(), 1)
        self.assertEqual(return_request.lines.first().quantity, 2)
        # Buyer confirmation + staff notification.
        self.assertEqual(len(mail.outbox), 2)
        recipients = {addr for message in mail.outbox for addr in message.to}
        self.assertIn("buyer@example.com", recipients)

    def test_request_return_rejects_non_delivered_order(self):
        self.order.status = Order.Status.SHIPPED
        self.order.save()

        with self.assertRaises(ValueError):
            request_return(
                self.order,
                [{"order_item": self.item, "quantity": 1}],
                reason=ReturnRequest.Reason.OTHER,
                requested_by=self.buyer,
            )

    def test_request_return_rejects_outside_return_window(self):
        self.shipment.delivered_at = timezone.now() - timedelta(days=30)
        self.shipment.save()

        with self.assertRaises(ValueError):
            request_return(
                self.order,
                [{"order_item": self.item, "quantity": 1}],
                reason=ReturnRequest.Reason.OTHER,
                requested_by=self.buyer,
            )

    def test_request_return_rejects_quantity_beyond_remaining(self):
        with self.assertRaises(ValueError):
            request_return(
                self.order,
                [{"order_item": self.item, "quantity": 4}],  # only 3 were ordered
                reason=ReturnRequest.Reason.OTHER,
                requested_by=self.buyer,
            )

    def test_request_return_prevents_double_returning_the_same_units(self):
        request_return(
            self.order,
            [{"order_item": self.item, "quantity": 2}],
            reason=ReturnRequest.Reason.OTHER,
            requested_by=self.buyer,
        )
        self.assertEqual(remaining_returnable_quantity(self.item), 1)

        with self.assertRaises(ValueError):
            request_return(
                self.order,
                [{"order_item": self.item, "quantity": 2}],  # only 1 left eligible
                reason=ReturnRequest.Reason.OTHER,
                requested_by=self.buyer,
            )

    def test_is_order_returnable(self):
        self.assertTrue(is_order_returnable(self.order))

        self.shipment.delivered_at = timezone.now() - timedelta(days=30)
        self.shipment.save()
        self.assertFalse(is_order_returnable(self.order))

    def test_approve_return_advances_status_and_sends_email(self):
        return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 1}],
            reason=ReturnRequest.Reason.OTHER,
            requested_by=self.buyer,
        )
        mail.outbox.clear()

        approve_return(return_request, reviewed_by=self.buyer, staff_notes="Looks fine")

        return_request.refresh_from_db()
        self.assertEqual(return_request.status, ReturnRequest.Status.APPROVED)
        self.assertEqual(return_request.staff_notes, "Looks fine")
        self.assertEqual(len(mail.outbox), 1)

    def test_approve_return_rejects_wrong_starting_status(self):
        return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 1}],
            reason=ReturnRequest.Reason.OTHER,
            requested_by=self.buyer,
        )
        approve_return(return_request, reviewed_by=self.buyer)

        with self.assertRaises(ValueError):
            approve_return(return_request, reviewed_by=self.buyer)

    def test_reject_return_advances_status_and_sends_email(self):
        return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 1}],
            reason=ReturnRequest.Reason.OTHER,
            requested_by=self.buyer,
        )
        mail.outbox.clear()

        reject_return(return_request, reviewed_by=self.buyer, staff_notes="Outside policy")

        return_request.refresh_from_db()
        self.assertEqual(return_request.status, ReturnRequest.Status.REJECTED)
        self.assertEqual(len(mail.outbox), 1)

    def test_mark_return_received_restocks_only_chosen_lines(self):
        return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 2}],
            reason=ReturnRequest.Reason.DEFECTIVE,
            requested_by=self.buyer,
        )
        approve_return(return_request, reviewed_by=self.buyer)
        line = return_request.lines.first()
        stock_before = self.product.stock_count

        mark_return_received(
            return_request,
            line_decisions={line.pk: {"restock": True, "condition_note": "Good condition"}},
            received_by=self.buyer,
        )

        return_request.refresh_from_db()
        line.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(return_request.status, ReturnRequest.Status.RECEIVED)
        self.assertTrue(line.restocked)
        self.assertEqual(self.product.stock_count, stock_before + 2)
        self.assertTrue(
            StockMovement.objects.filter(
                return_request=return_request, movement_type=StockMovement.MovementType.RETURNED
            ).exists()
        )

    def test_mark_return_received_defaults_to_not_restocked(self):
        return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 1}],
            reason=ReturnRequest.Reason.DEFECTIVE,
            requested_by=self.buyer,
        )
        approve_return(return_request, reviewed_by=self.buyer)
        stock_before = self.product.stock_count

        # No decision supplied for this line at all — should default to not restocked.
        mark_return_received(return_request, line_decisions={}, received_by=self.buyer)

        self.product.refresh_from_db()
        line = return_request.lines.first()
        line.refresh_from_db()
        self.assertFalse(line.restocked)
        self.assertEqual(self.product.stock_count, stock_before)

    def test_mark_return_received_requires_approved_status(self):
        return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 1}],
            reason=ReturnRequest.Reason.OTHER,
            requested_by=self.buyer,
        )
        with self.assertRaises(ValueError):
            mark_return_received(return_request, line_decisions={}, received_by=self.buyer)

    def test_cancel_return_request_only_allowed_while_requested(self):
        return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 1}],
            reason=ReturnRequest.Reason.OTHER,
            requested_by=self.buyer,
        )
        cancel_return_request(return_request, cancelled_by=self.buyer)
        return_request.refresh_from_db()
        self.assertEqual(return_request.status, ReturnRequest.Status.CANCELLED)

        another = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 1}],
            reason=ReturnRequest.Reason.OTHER,
            requested_by=self.buyer,
        )
        approve_return(another, reviewed_by=self.buyer)
        with self.assertRaises(ValueError):
            cancel_return_request(another, cancelled_by=self.buyer)

    def test_approve_return_creates_return_shipment(self):
        return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 1}],
            reason=ReturnRequest.Reason.OTHER,
            requested_by=self.buyer,
        )
        approve_return(return_request, reviewed_by=self.buyer)

        return_shipment = ReturnShipment.objects.get(return_request=return_request)
        self.assertEqual(return_shipment.status, ReturnShipment.Status.AWAITING_DISPATCH)

    def test_update_return_shipment_tracking_flips_to_shipped(self):
        return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 1}],
            reason=ReturnRequest.Reason.OTHER,
            requested_by=self.buyer,
        )
        approve_return(return_request, reviewed_by=self.buyer)

        return_shipment = update_return_shipment_tracking(
            return_request, carrier=Shipment.Carrier.INDIA_POST, tracking_number="RT123", tracking_url=""
        )

        self.assertEqual(return_shipment.status, ReturnShipment.Status.SHIPPED)
        self.assertIsNotNone(return_shipment.shipped_at)
        self.assertEqual(return_shipment.tracking_number, "RT123")

    def test_update_return_shipment_tracking_rejects_before_approval(self):
        return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 1}],
            reason=ReturnRequest.Reason.OTHER,
            requested_by=self.buyer,
        )
        with self.assertRaises(ValueError):
            update_return_shipment_tracking(return_request, carrier=Shipment.Carrier.SELF)

    def test_mark_return_received_flips_return_shipment_to_delivered(self):
        return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 1}],
            reason=ReturnRequest.Reason.OTHER,
            requested_by=self.buyer,
        )
        approve_return(return_request, reviewed_by=self.buyer)
        update_return_shipment_tracking(return_request, carrier=Shipment.Carrier.SELF, tracking_number="RT1")

        mark_return_received(return_request, line_decisions={}, received_by=self.buyer)

        return_shipment = ReturnShipment.objects.get(return_request=return_request)
        self.assertEqual(return_shipment.status, ReturnShipment.Status.DELIVERED)
        self.assertIsNotNone(return_shipment.delivered_at)


class ReturnRefundIntegrationTestCase(TestCase):
    """Covers initiate_return_refund and its round-trip through payments.pipeline."""

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
            status=Order.Status.DELIVERED,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="123 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00"),
        )
        self.item = OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_title=self.product.title,
            product_sku=self.product.sku,
            unit_price=Decimal("999.00"),
            quantity=1,
            hsn_code="6204",
            gst_rate=Decimal("5.00"),
        )
        Shipment.objects.create(order=self.order, status=Shipment.Status.DELIVERED, delivered_at=timezone.now())
        self.payment = Payment.objects.create(
            order=self.order,
            user=self.buyer,
            gateway_order_id="order_1",
            gateway_payment_id="pay_1",
            amount=Decimal("999.00"),
            status=Payment.Status.CAPTURED,
        )

        self.fake_gateway = FakeGateway()
        patcher = mock.patch("payments.services.get_gateway", return_value=self.fake_gateway)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.return_request = request_return(
            self.order,
            [{"order_item": self.item, "quantity": 1}],
            reason=ReturnRequest.Reason.DEFECTIVE,
            requested_by=self.buyer,
        )
        approve_return(self.return_request, reviewed_by=self.buyer)
        mark_return_received(self.return_request, line_decisions={}, received_by=self.buyer)

    def test_initiate_return_refund_links_refund_to_return_request(self):
        refund = initiate_return_refund(self.return_request, Decimal("999.00"), initiated_by=self.buyer)

        self.return_request.refresh_from_db()
        self.assertEqual(refund.return_request_id, self.return_request.pk)
        self.assertEqual(self.return_request.status, ReturnRequest.Status.REFUND_INITIATED)

    def test_initiate_return_refund_requires_received_status(self):
        self.return_request.status = ReturnRequest.Status.APPROVED
        self.return_request.save()

        with self.assertRaises(ValueError):
            initiate_return_refund(self.return_request, Decimal("999.00"), initiated_by=self.buyer)

    def test_pipeline_advances_return_to_refunded_once_processed(self):
        refund = initiate_return_refund(self.return_request, Decimal("999.00"), initiated_by=self.buyer)
        self.payment.refunded_amount = Decimal("999.00")
        self.payment.refund_status = Payment.RefundStatus.FULL
        self.payment.save()
        refund.status = refund.Status.PROCESSED
        refund.save()

        pipeline.on_refund_processed(refund)

        self.return_request.refresh_from_db()
        self.assertEqual(self.return_request.status, ReturnRequest.Status.REFUNDED)

    def test_pipeline_reverts_return_to_received_on_refund_failure(self):
        refund = initiate_return_refund(self.return_request, Decimal("999.00"), initiated_by=self.buyer)
        refund.status = refund.Status.FAILED
        refund.save()

        pipeline.on_refund_failed(refund)

        self.return_request.refresh_from_db()
        self.assertEqual(self.return_request.status, ReturnRequest.Status.RECEIVED)

    def test_pipeline_does_not_touch_unrelated_refunds(self):
        """A refund with no return_request set shouldn't error or touch any ReturnRequest."""
        standalone_refund = PaymentService().initiate_refund(self.payment, Decimal("100.00"), initiated_by=self.buyer)
        standalone_refund.status = standalone_refund.Status.PROCESSED
        standalone_refund.save()

        pipeline.on_refund_processed(standalone_refund)  # should not raise

        self.return_request.refresh_from_db()
        self.assertEqual(self.return_request.status, ReturnRequest.Status.RECEIVED)
