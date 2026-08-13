"""Tests for the stock reservation/movement service functions."""

from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from cart.models import Order, OrderItem
from catalog.models import Category, Department, Product, Subcategory
from inventory.models import StockMovement
from inventory.services import (
    InsufficientStockError,
    convert_reservation_to_sale,
    record_adjustment,
    record_restock,
    release_reservation,
    reserve_stock,
    restore_stock_for_cancellation,
)


class InventoryServiceTestCase(TestCase):
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
        self.order = Order.objects.create(
            user=buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
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
            quantity=3,
        )

    def test_reserve_stock_increments_reserved_count_and_logs_movement(self):
        reserve_stock(self.order)

        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved_count, 3)
        self.assertEqual(self.product.available_to_sell, 7)
        movement = StockMovement.objects.get(order=self.order)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.RESERVED)
        self.assertEqual(movement.reserved_delta, 3)
        self.assertEqual(movement.stock_delta, 0)

    def test_reserve_stock_raises_when_insufficient_and_reserves_nothing(self):
        self.item.quantity = 999
        self.item.save()

        with self.assertRaises(InsufficientStockError):
            reserve_stock(self.order)

        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved_count, 0)
        self.assertFalse(StockMovement.objects.filter(order=self.order).exists())

    def test_release_reservation_decrements_reserved_count(self):
        reserve_stock(self.order)

        release_reservation(self.order)

        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved_count, 0)
        self.assertEqual(
            StockMovement.objects.filter(order=self.order, movement_type=StockMovement.MovementType.RELEASED).count(),
            1,
        )

    def test_release_reservation_clamps_at_zero_when_nothing_reserved(self):
        # Never reserved in the first place — shouldn't go negative.
        release_reservation(self.order)

        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved_count, 0)

    def test_convert_reservation_to_sale_decrements_both_counters(self):
        reserve_stock(self.order)

        convert_reservation_to_sale(self.order)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_count, 7)  # 10 - 3
        self.assertEqual(self.product.reserved_count, 0)
        movement = StockMovement.objects.get(order=self.order, movement_type=StockMovement.MovementType.SOLD)
        self.assertEqual(movement.stock_delta, -3)
        self.assertEqual(movement.reserved_delta, -3)

    def test_convert_reservation_to_sale_floors_at_zero_when_oversold(self):
        # Simulates payment capturing after stock was somehow already
        # depleted elsewhere in the gap between reservation and capture.
        reserve_stock(self.order)
        self.product.stock_count = 1
        self.product.save()

        convert_reservation_to_sale(self.order)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_count, 0)

    def test_restore_stock_for_cancellation_increments_stock_count(self):
        reserve_stock(self.order)
        convert_reservation_to_sale(self.order)  # simulates payment having captured

        restore_stock_for_cancellation(self.order)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_count, 10)  # back to original
        self.assertTrue(
            StockMovement.objects.filter(order=self.order, movement_type=StockMovement.MovementType.RESTORED).exists()
        )

    def test_record_restock_increments_stock_and_requires_positive_quantity(self):
        record_restock(self.product, quantity=5, note="Supplier delivery")

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_count, 15)

        with self.assertRaises(ValueError):
            record_restock(self.product, quantity=0)
        with self.assertRaises(ValueError):
            record_restock(self.product, quantity=-1)

    def test_record_adjustment_allows_negative_delta_and_floors_at_zero(self):
        record_adjustment(self.product, delta=-3, note="Damaged units")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_count, 7)

        record_adjustment(self.product, delta=-999, note="Massive correction")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_count, 0)

        with self.assertRaises(ValueError):
            record_adjustment(self.product, delta=0)
