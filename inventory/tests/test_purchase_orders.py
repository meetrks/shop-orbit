"""Tests for supplier/purchase-order service functions."""

from decimal import Decimal

from django.test import TestCase

from catalog.models import Category, Department, Product, Subcategory
from inventory.models import PurchaseOrder, StockMovement, Supplier
from inventory.services import (
    create_purchase_order,
    create_purchase_order_from_low_stock,
    mark_purchase_order_ordered,
    receive_purchase_order_line,
)


class PurchaseOrderTestCase(TestCase):
    def setUp(self):
        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        subcategory = Subcategory.objects.create(category=category, name="Sarees")
        self.product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
            stock_count=3,
            low_stock_threshold=5,
        )
        self.supplier = Supplier.objects.create(name="Fabric Traders", email="orders@fabrictraders.example")

    def test_create_purchase_order_creates_draft_with_lines(self):
        po = create_purchase_order(
            self.supplier,
            [{"product": self.product, "quantity_ordered": 20, "unit_cost": Decimal("400.00")}],
        )

        self.assertEqual(po.status, PurchaseOrder.Status.DRAFT)
        self.assertTrue(po.po_number.startswith("PO-"))
        line = po.lines.get()
        self.assertEqual(line.quantity_ordered, 20)
        self.assertEqual(po.total_cost, Decimal("8000.00"))

    def test_create_purchase_order_rejects_empty_lines(self):
        with self.assertRaises(ValueError):
            create_purchase_order(self.supplier, [])

    def test_mark_purchase_order_ordered_requires_draft_status(self):
        po = create_purchase_order(
            self.supplier, [{"product": self.product, "quantity_ordered": 10, "unit_cost": Decimal("400.00")}]
        )

        mark_purchase_order_ordered(po)
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.ORDERED)

        with self.assertRaises(ValueError):
            mark_purchase_order_ordered(po)  # already ordered, not draft

    def test_receive_purchase_order_line_full_receipt_marks_po_received(self):
        po = create_purchase_order(
            self.supplier, [{"product": self.product, "quantity_ordered": 10, "unit_cost": Decimal("400.00")}]
        )
        mark_purchase_order_ordered(po)
        line = po.lines.get()

        receive_purchase_order_line(line, 10)

        line.refresh_from_db()
        po.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(line.quantity_received, 10)
        self.assertTrue(line.is_fully_received)
        self.assertEqual(po.status, PurchaseOrder.Status.RECEIVED)
        self.assertEqual(self.product.stock_count, 13)  # 3 + 10
        movement = StockMovement.objects.get(purchase_order=po)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.RESTOCKED)
        self.assertEqual(movement.stock_delta, 10)

    def test_receive_purchase_order_line_partial_receipt(self):
        po = create_purchase_order(
            self.supplier, [{"product": self.product, "quantity_ordered": 10, "unit_cost": Decimal("400.00")}]
        )
        mark_purchase_order_ordered(po)
        line = po.lines.get()

        receive_purchase_order_line(line, 6)

        line.refresh_from_db()
        po.refresh_from_db()
        self.assertEqual(line.quantity_received, 6)
        self.assertFalse(line.is_fully_received)
        self.assertEqual(po.status, PurchaseOrder.Status.PARTIALLY_RECEIVED)

        receive_purchase_order_line(line, 4)
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.RECEIVED)

    def test_receive_purchase_order_line_rejects_over_receipt(self):
        po = create_purchase_order(
            self.supplier, [{"product": self.product, "quantity_ordered": 10, "unit_cost": Decimal("400.00")}]
        )
        line = po.lines.get()

        with self.assertRaises(ValueError):
            receive_purchase_order_line(line, 11)

    def test_create_purchase_order_from_low_stock_suggests_reorder_quantity(self):
        self.product.default_supplier = self.supplier
        self.product.save()
        # stock_count=3, low_stock_threshold=5 -> available_to_sell=3 <= 5, suggest 2*5-3=7

        po = create_purchase_order_from_low_stock(self.supplier)

        line = po.lines.get()
        self.assertEqual(line.product, self.product)
        self.assertEqual(line.quantity_ordered, 7)

    def test_create_purchase_order_from_low_stock_raises_when_nothing_low(self):
        self.product.default_supplier = self.supplier
        self.product.stock_count = 100
        self.product.save()

        with self.assertRaises(ValueError):
            create_purchase_order_from_low_stock(self.supplier)
