"""Tests for the stock-reservation expiry sweep."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from cart.models import Order, OrderItem
from catalog.models import Category, Department, Product, Subcategory
from inventory.services import reserve_stock
from inventory.tasks import release_expired_reservations


@override_settings(RESERVATION_TIMEOUT_MINUTES=30)
class ReleaseExpiredReservationsTestCase(TestCase):
    def setUp(self):
        buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
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
        self.buyer = buyer

    def _make_order(self, *, quantity=2):
        order = Order.objects.create(
            user=self.buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00") * quantity,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_title=self.product.title,
            product_sku=self.product.sku,
            unit_price=Decimal("999.00"),
            quantity=quantity,
        )
        return order

    def test_releases_reservation_and_marks_order_expired(self):
        order = self._make_order(quantity=2)
        reserve_stock(order)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(minutes=45))

        released_count = release_expired_reservations()

        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(released_count, 1)
        self.assertEqual(order.status, Order.Status.EXPIRED)
        self.assertEqual(self.product.reserved_count, 0)

    def test_leaves_recent_orders_alone(self):
        order = self._make_order(quantity=2)
        reserve_stock(order)
        # created_at defaults to now — well within the 30-minute window.

        released_count = release_expired_reservations()

        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(released_count, 0)
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.assertEqual(self.product.reserved_count, 2)

    def test_leaves_non_awaiting_payment_orders_alone(self):
        order = self._make_order(quantity=2)
        reserve_stock(order)
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(minutes=45), status=Order.Status.PAYMENT_CONFIRMED
        )

        released_count = release_expired_reservations()

        self.product.refresh_from_db()
        self.assertEqual(released_count, 0)
        self.assertEqual(self.product.reserved_count, 2)  # untouched
