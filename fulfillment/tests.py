"""
Tests for GST invoice generation (numbering, CGST/SGST vs IGST split,
idempotency) and packing slip generation. PDF generation is smoke-tested
(non-empty, well-formed output) rather than asserting exact layout.
"""

from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings

from accounts.models import User
from cart.models import Order, OrderItem
from catalog.models import Category, Department, Product, Subcategory

from .couriers import delhivery
from .couriers.delhivery import DelhiveryAPIError
from .models import Invoice, InvoiceNumberSequence, PackingSlip, PincodeServiceability, Shipment, ShipmentStatusHistory
from .services import (
    assign_delhivery_waybill,
    generate_invoice,
    generate_packing_slip,
    is_pincode_serviceable,
    pincode_block_reason,
    sync_shipment_tracking,
)
from .tasks import sync_all_shipment_tracking


@override_settings(COMPANY_STATE="Karnataka", INVOICE_SERIES_PREFIX="INV")
class InvoiceGenerationTestCase(TestCase):
    def setUp(self):
        buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        subcategory = Subcategory.objects.create(category=category, name="Sarees")
        self.product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("1050.00"),
            hsn_code="6204",
            gst_rate=Decimal("5.00"),
        )
        self.buyer = buyer

    def _make_order(self, *, shipping_state, quantity=1):
        order = Order.objects.create(
            user=self.buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
            shipping_city="Some City",
            shipping_state=shipping_state,
            shipping_postal_code="560001",
            subtotal_amount=Decimal("1050.00") * quantity,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_title=self.product.title,
            product_sku=self.product.sku,
            unit_price=Decimal("1050.00"),
            quantity=quantity,
            hsn_code="6204",
            gst_rate=Decimal("5.00"),
        )
        return order

    def test_intrastate_order_splits_tax_into_cgst_and_sgst(self):
        order = self._make_order(shipping_state="Karnataka")
        invoice = generate_invoice(order)

        self.assertEqual(invoice.igst_amount, Decimal("0.00"))
        self.assertGreater(invoice.cgst_amount, Decimal("0.00"))
        self.assertEqual(invoice.cgst_amount, invoice.sgst_amount)
        self.assertEqual(invoice.cgst_amount + invoice.sgst_amount, invoice.total_tax)

    def test_interstate_order_uses_igst(self):
        order = self._make_order(shipping_state="Maharashtra")
        invoice = generate_invoice(order)

        self.assertEqual(invoice.cgst_amount, Decimal("0.00"))
        self.assertEqual(invoice.sgst_amount, Decimal("0.00"))
        self.assertGreater(invoice.igst_amount, Decimal("0.00"))
        self.assertEqual(invoice.igst_amount, invoice.total_tax)

    def test_tax_inclusive_price_is_correctly_backed_out(self):
        # unit_price 1050 at 5% GST: taxable value 1000.00, tax 50.00.
        order = self._make_order(shipping_state="Karnataka")
        invoice = generate_invoice(order)

        self.assertEqual(invoice.taxable_amount, Decimal("1000.00"))
        self.assertEqual(invoice.total_tax, Decimal("50.00"))
        self.assertEqual(invoice.total_amount, order.total_amount)

    def test_generate_invoice_is_idempotent(self):
        order = self._make_order(shipping_state="Karnataka")
        first = generate_invoice(order)
        second = generate_invoice(order)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Invoice.objects.filter(order=order).count(), 1)

    def test_invoice_numbers_are_sequential_within_a_financial_year(self):
        order_a = self._make_order(shipping_state="Karnataka")
        order_b = self._make_order(shipping_state="Karnataka")

        invoice_a = generate_invoice(order_a)
        invoice_b = generate_invoice(order_b)

        self.assertNotEqual(invoice_a.invoice_number, invoice_b.invoice_number)
        self.assertEqual(InvoiceNumberSequence.objects.count(), 1)
        self.assertEqual(InvoiceNumberSequence.objects.get().last_number, 2)

    def test_invoice_pdf_is_generated(self):
        order = self._make_order(shipping_state="Karnataka")
        invoice = generate_invoice(order)

        self.assertTrue(invoice.pdf.name)
        content = invoice.pdf.read()
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertGreater(len(content), 100)

    def test_generate_packing_slip_is_idempotent_and_produces_pdf(self):
        order = self._make_order(shipping_state="Karnataka")
        first = generate_packing_slip(order)
        second = generate_packing_slip(order)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PackingSlip.objects.filter(order=order).count(), 1)
        content = first.pdf.read()
        self.assertTrue(content.startswith(b"%PDF"))


class ShipmentStatusHistoryTestCase(TestCase):
    def setUp(self):
        buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        subcategory = Subcategory.objects.create(category=category, name="Sarees")
        product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
        )
        order = Order.objects.create(
            user=buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00"),
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            product_title=product.title,
            product_sku=product.sku,
            unit_price=Decimal("999.00"),
            quantity=1,
            hsn_code="6204",
            gst_rate=Decimal("5.00"),
        )
        self.staff = User.objects.create_user(
            email="staff@example.com", password="pw", full_name="Staff One", is_staff=True
        )
        self.shipment = Shipment.objects.create(order=order)

    def test_status_change_creates_history_row(self):
        self.shipment.status = Shipment.Status.PICKED_UP
        self.shipment.save()

        history = ShipmentStatusHistory.objects.get(shipment=self.shipment)
        self.assertEqual(history.previous_status, Shipment.Status.PENDING)
        self.assertEqual(history.new_status, Shipment.Status.PICKED_UP)
        self.assertIsNone(history.changed_by)

    def test_unchanged_status_save_writes_no_history(self):
        self.shipment.tracking_number = "TRACK123"
        self.shipment.save()

        self.assertEqual(ShipmentStatusHistory.objects.filter(shipment=self.shipment).count(), 0)

    def test_status_change_actor_is_attributed(self):
        self.shipment._status_change_actor = self.staff
        self.shipment.status = Shipment.Status.DELIVERED
        self.shipment.save()

        history = ShipmentStatusHistory.objects.get(shipment=self.shipment)
        self.assertEqual(history.changed_by, self.staff)


class PincodeServiceabilityTestCase(TestCase):
    def test_pincode_with_no_row_is_serviceable_by_default(self):
        self.assertTrue(is_pincode_serviceable("560001"))

    def test_explicit_block_makes_pincode_unserviceable(self):
        PincodeServiceability.objects.create(postal_code="999999", is_serviceable=False, note="Too remote.")
        self.assertFalse(is_pincode_serviceable("999999"))
        self.assertEqual(pincode_block_reason("999999"), "Too remote.")

    def test_block_reason_falls_back_to_generic_message_without_a_note(self):
        PincodeServiceability.objects.create(postal_code="999998", is_serviceable=False)
        self.assertEqual(pincode_block_reason("999998"), "We currently don't deliver to this pincode.")

    def test_row_marked_serviceable_does_not_block(self):
        PincodeServiceability.objects.create(postal_code="560001", is_serviceable=True)
        self.assertTrue(is_pincode_serviceable("560001"))


def _make_order(buyer, product, *, quantity=1):
    order = Order.objects.create(
        user=buyer,
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
        product=product,
        product_title=product.title,
        product_sku=product.sku,
        unit_price=Decimal("999.00"),
        quantity=quantity,
        hsn_code="6204",
        gst_rate=Decimal("5.00"),
    )
    return order


def _fake_response(*, ok=True, status_code=200, json_body=None, text="", content=b""):
    response = mock.Mock()
    response.ok = ok
    response.status_code = status_code
    response.json.return_value = json_body or {}
    response.text = text
    response.content = content
    return response


@override_settings(DELHIVERY_API_TOKEN="test-token", DELHIVERY_PICKUP_LOCATION_NAME="Test Warehouse")
class DelhiveryClientTestCase(TestCase):
    """Delhivery's own API isn't reachable in tests, so requests.post/get are mocked at the transport boundary."""

    def setUp(self):
        buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        subcategory = Subcategory.objects.create(category=category, name="Sarees")
        product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
        )
        self.order = _make_order(buyer, product)

    @override_settings(DELHIVERY_API_TOKEN="")
    def test_create_waybill_raises_when_not_configured(self):
        with self.assertRaises(DelhiveryAPIError):
            delhivery.create_waybill(self.order)

    @mock.patch("fulfillment.couriers.delhivery.requests.post")
    def test_create_waybill_returns_awb_on_success(self, mock_post):
        mock_post.return_value = _fake_response(json_body={"packages": [{"waybill": "AWB123", "status": "Success"}]})

        waybill = delhivery.create_waybill(self.order)

        self.assertEqual(waybill, "AWB123")
        sent_data = mock_post.call_args.kwargs["data"]
        self.assertEqual(sent_data["format"], "json")
        self.assertIn(self.order.order_number, sent_data["data"])

    @mock.patch("fulfillment.couriers.delhivery.requests.post")
    def test_create_waybill_raises_when_no_waybill_returned(self, mock_post):
        mock_post.return_value = _fake_response(json_body={"packages": [{"status": "Fail", "remarks": "Bad pincode"}]})

        with self.assertRaises(DelhiveryAPIError):
            delhivery.create_waybill(self.order)

    @mock.patch("fulfillment.couriers.delhivery.requests.post")
    def test_create_waybill_raises_on_http_error(self, mock_post):
        mock_post.return_value = _fake_response(ok=False, status_code=500, text="Internal error")

        with self.assertRaises(DelhiveryAPIError):
            delhivery.create_waybill(self.order)

    @mock.patch("fulfillment.couriers.delhivery.requests.get")
    def test_track_returns_parsed_json(self, mock_get):
        mock_get.return_value = _fake_response(
            json_body={"ShipmentData": [{"Shipment": {"Status": {"Status": "Delivered"}}}]}
        )

        result = delhivery.track("AWB123")

        self.assertEqual(result["ShipmentData"][0]["Shipment"]["Status"]["Status"], "Delivered")
        self.assertEqual(mock_get.call_args.kwargs["params"], {"waybill": "AWB123"})

    def test_map_tracking_status_known_statuses(self):
        for raw, expected in [
            ("Delivered", Shipment.Status.DELIVERED),
            ("In Transit", Shipment.Status.IN_TRANSIT),
            ("Out for Delivery", Shipment.Status.OUT_FOR_DELIVERY),
            ("RTO", Shipment.Status.RETURNED),
            ("NDR", Shipment.Status.NDR),
        ]:
            response = {"ShipmentData": [{"Shipment": {"Status": {"Status": raw}}}]}
            self.assertEqual(delhivery.map_tracking_status(response), expected, raw)

    def test_map_tracking_status_unrecognized_returns_none(self):
        response = {"ShipmentData": [{"Shipment": {"Status": {"Status": "Some New Status Delhivery Invented"}}}]}
        self.assertIsNone(delhivery.map_tracking_status(response))

    def test_map_tracking_status_unexpected_shape_returns_none(self):
        self.assertIsNone(delhivery.map_tracking_status({"unexpected": "shape"}))


@override_settings(DELHIVERY_API_TOKEN="test-token", DELHIVERY_PICKUP_LOCATION_NAME="Test Warehouse")
class ShipmentDelhiveryServiceTestCase(TestCase):
    def setUp(self):
        buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        self.staff = User.objects.create_user(
            email="staff@example.com", password="pw", full_name="Staff One", is_staff=True
        )
        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        subcategory = Subcategory.objects.create(category=category, name="Sarees")
        product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
        )
        order = _make_order(buyer, product)
        self.shipment = Shipment.objects.create(order=order)

    @mock.patch("fulfillment.services.delhivery.create_waybill", return_value="AWB123")
    def test_assign_delhivery_waybill_updates_shipment(self, _mock_create):
        assign_delhivery_waybill(self.shipment, actor=self.staff)

        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.carrier, Shipment.Carrier.DELHIVERY)
        self.assertEqual(self.shipment.tracking_number, "AWB123")
        self.assertIn("AWB123", self.shipment.tracking_url)

        history = ShipmentStatusHistory.objects.filter(shipment=self.shipment)
        # AWB assignment doesn't itself change status, so no history row is expected here.
        self.assertEqual(history.count(), 0)

    def test_sync_shipment_tracking_noop_without_waybill(self):
        self.assertFalse(sync_shipment_tracking(self.shipment))

    @mock.patch("fulfillment.services.delhivery.track")
    def test_sync_shipment_tracking_updates_status(self, mock_track):
        self.shipment.carrier = Shipment.Carrier.DELHIVERY
        self.shipment.tracking_number = "AWB123"
        self.shipment.save()
        mock_track.return_value = {"ShipmentData": [{"Shipment": {"Status": {"Status": "Delivered"}}}]}

        updated = sync_shipment_tracking(self.shipment, actor=self.staff)

        self.assertTrue(updated)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, Shipment.Status.DELIVERED)
        self.assertIsNotNone(self.shipment.delivered_at)

    @mock.patch("fulfillment.services.delhivery.track")
    def test_sync_shipment_tracking_sends_ndr_email(self, mock_track):
        self.shipment.carrier = Shipment.Carrier.DELHIVERY
        self.shipment.tracking_number = "AWB123"
        self.shipment.save()
        mock_track.return_value = {"ShipmentData": [{"Shipment": {"Status": {"Status": "NDR"}}}]}

        sync_shipment_tracking(self.shipment)

        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, Shipment.Status.NDR)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("delivery attempt failed", mail.outbox[0].subject.lower())

    @mock.patch("fulfillment.services.delhivery.track")
    def test_sync_shipment_tracking_unrecognized_status_leaves_shipment_unchanged(self, mock_track):
        self.shipment.carrier = Shipment.Carrier.DELHIVERY
        self.shipment.tracking_number = "AWB123"
        self.shipment.save()
        mock_track.return_value = {"ShipmentData": [{"Shipment": {"Status": {"Status": "Something Unknown"}}}]}

        updated = sync_shipment_tracking(self.shipment)

        self.assertFalse(updated)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, Shipment.Status.PENDING)


class SyncAllShipmentTrackingTaskTestCase(TestCase):
    @override_settings(DELHIVERY_API_TOKEN="")
    def test_noop_when_not_configured(self):
        with mock.patch("fulfillment.tasks.sync_shipment_tracking") as mock_sync:
            sync_all_shipment_tracking()
        mock_sync.assert_not_called()

    @override_settings(DELHIVERY_API_TOKEN="test-token")
    def test_syncs_active_delhivery_shipments_only(self):
        buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        subcategory = Subcategory.objects.create(category=category, name="Sarees")
        product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
        )

        active_shipment = Shipment.objects.create(
            order=_make_order(buyer, product), carrier=Shipment.Carrier.DELHIVERY, tracking_number="AWB1"
        )
        Shipment.objects.create(order=_make_order(buyer, product))  # no carrier/tracking number — should be skipped
        delivered_shipment = Shipment.objects.create(
            order=_make_order(buyer, product),
            carrier=Shipment.Carrier.DELHIVERY,
            tracking_number="AWB2",
            status=Shipment.Status.DELIVERED,
        )

        with mock.patch("fulfillment.tasks.sync_shipment_tracking") as mock_sync:
            sync_all_shipment_tracking()

        synced_shipments = {call.args[0].pk for call in mock_sync.call_args_list}
        self.assertEqual(synced_shipments, {active_shipment.pk})
        self.assertNotIn(delivered_shipment.pk, synced_shipments)
