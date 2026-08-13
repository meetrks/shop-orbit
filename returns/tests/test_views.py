"""Tests for the buyer-facing return-request views."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from cart.models import Order, OrderItem
from catalog.models import Category, Department, Product, Subcategory
from fulfillment.models import Shipment
from returns.models import ReturnRequest, ReturnShipment
from returns.services import approve_return


class RequestReturnViewTestCase(TestCase):
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
        self.client.force_login(self.buyer)

    def test_get_shows_eligible_items(self):
        response = self.client.get(reverse("returns:request_return", args=[self.order.order_number]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Silk Saree")

    def test_post_creates_return_request_and_redirects(self):
        response = self.client.post(
            reverse("returns:request_return", args=[self.order.order_number]),
            data={f"quantity_{self.item.pk}": "1", "reason": ReturnRequest.Reason.DEFECTIVE, "comments": "Damaged"},
        )
        self.assertRedirects(response, reverse("cart:order_confirmation", args=[self.order.order_number]))
        self.assertTrue(ReturnRequest.objects.filter(order=self.order, user=self.buyer).exists())

    def test_post_with_no_quantities_selected_shows_error(self):
        response = self.client.post(
            reverse("returns:request_return", args=[self.order.order_number]),
            data={f"quantity_{self.item.pk}": "0", "reason": ReturnRequest.Reason.OTHER},
        )
        self.assertRedirects(response, reverse("returns:request_return", args=[self.order.order_number]))
        self.assertFalse(ReturnRequest.objects.filter(order=self.order).exists())

    def test_buyer_cannot_request_return_for_someone_elses_order(self):
        other_buyer = User.objects.create_user(email="other@example.com", password="pw", full_name="Other Buyer")
        self.client.force_login(other_buyer)

        response = self.client.get(reverse("returns:request_return", args=[self.order.order_number]))
        self.assertEqual(response.status_code, 404)


class CancelReturnRequestViewTestCase(TestCase):
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
            status=Order.Status.DELIVERED,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="123 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00"),
        )
        item = OrderItem.objects.create(
            order=self.order,
            product=product,
            product_title=product.title,
            product_sku=product.sku,
            unit_price=Decimal("999.00"),
            quantity=1,
            hsn_code="6204",
            gst_rate=Decimal("5.00"),
        )
        self.return_request = ReturnRequest.objects.create(
            order=self.order, user=self.buyer, reason=ReturnRequest.Reason.OTHER
        )
        from returns.models import ReturnRequestLine

        ReturnRequestLine.objects.create(return_request=self.return_request, order_item=item, quantity=1)

    def test_buyer_can_cancel_own_pending_return(self):
        self.client.force_login(self.buyer)
        response = self.client.post(reverse("returns:cancel_return_request", args=[self.return_request.pk]))
        self.assertRedirects(response, reverse("cart:order_confirmation", args=[self.order.order_number]))
        self.return_request.refresh_from_db()
        self.assertEqual(self.return_request.status, ReturnRequest.Status.CANCELLED)

    def test_buyer_cannot_cancel_someone_elses_return(self):
        other_buyer = User.objects.create_user(email="other@example.com", password="pw", full_name="Other Buyer")
        self.client.force_login(other_buyer)
        response = self.client.post(reverse("returns:cancel_return_request", args=[self.return_request.pk]))
        self.assertEqual(response.status_code, 404)


class UpdateReturnShipmentViewTestCase(TestCase):
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
            status=Order.Status.DELIVERED,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="123 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00"),
        )
        item = OrderItem.objects.create(
            order=self.order,
            product=product,
            product_title=product.title,
            product_sku=product.sku,
            unit_price=Decimal("999.00"),
            quantity=1,
            hsn_code="6204",
            gst_rate=Decimal("5.00"),
        )
        self.return_request = ReturnRequest.objects.create(
            order=self.order, user=self.buyer, reason=ReturnRequest.Reason.OTHER
        )
        from returns.models import ReturnRequestLine

        ReturnRequestLine.objects.create(return_request=self.return_request, order_item=item, quantity=1)
        approve_return(self.return_request, reviewed_by=self.buyer)
        self.client.force_login(self.buyer)

    def test_buyer_can_submit_tracking(self):
        response = self.client.post(
            reverse("returns:update_return_shipment", args=[self.return_request.pk]),
            data={"carrier": Shipment.Carrier.INDIA_POST, "tracking_number": "RT123", "tracking_url": ""},
        )
        self.assertRedirects(response, reverse("cart:order_confirmation", args=[self.order.order_number]))
        return_shipment = ReturnShipment.objects.get(return_request=self.return_request)
        self.assertEqual(return_shipment.status, ReturnShipment.Status.SHIPPED)
        self.assertEqual(return_shipment.tracking_number, "RT123")

    def test_non_owner_gets_404(self):
        other_buyer = User.objects.create_user(email="other@example.com", password="pw", full_name="Other Buyer")
        self.client.force_login(other_buyer)
        response = self.client.post(
            reverse("returns:update_return_shipment", args=[self.return_request.pk]),
            data={"carrier": Shipment.Carrier.SELF},
        )
        self.assertEqual(response.status_code, 404)
