"""Tests for the accounts app: mainly the store dashboard (staff-only, store-wide)."""

import json
from decimal import Decimal
from unittest import mock

from auditlog.models import LogEntry
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Address, User
from cart.models import Order, OrderItem
from catalog.models import Category, Department, Product, Subcategory
from payments.models import Payment
from payments.tests.fakes import FakeGateway


class StoreDashboardTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@example.com", password="pw", full_name="Staff One", is_staff=True
        )
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")

        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        subcategory_a = Subcategory.objects.create(category=category, name="Sarees")
        subcategory_b = Subcategory.objects.create(category=category, name="Kurtas")

        # Two products under different subcategories — stands in for what
        # used to be "two different sellers' listings", proving the
        # dashboard is store-wide, not scoped to whoever's logged in.
        self.product_a = Product.objects.create(
            subcategory=subcategory_a,
            title="Silk Saree",
            sku="SKU-A",
            price=Decimal("999.00"),
            stock_count=10,
        )
        self.product_b = Product.objects.create(
            subcategory=subcategory_b,
            title="Cotton Kurta",
            sku="SKU-B",
            price=Decimal("499.00"),
            stock_count=2,
            low_stock_threshold=5,
        )

    def test_staff_can_load_the_overview(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard"))

        self.assertEqual(response.status_code, 200)

    def test_non_staff_is_redirected_away(self):
        self.client.force_login(self.buyer)

        response = self.client.get(reverse("accounts:store_dashboard"))

        self.assertEqual(response.status_code, 302)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("accounts:store_dashboard"))

        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('accounts:store_dashboard')}",
        )

    def test_stock_health_reflects_low_stock_product(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard"))

        # product_b: stock_count=2, low_stock_threshold=5, reserved_count=0
        # -> low stock (0 < 2 <= 0+5), not out of stock.
        self.assertEqual(response.context["store_low_stock_count"], 1)
        self.assertEqual(response.context["store_out_of_stock_count"], 0)
        self.assertEqual(response.context["store_product_count"], 2)

    def test_chart_data_is_valid_json(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard"))
        content = response.content.decode()

        for script_id in [
            "revenue-chart-labels",
            "revenue-chart-totals",
            "status-chart-labels",
            "status-chart-counts",
        ]:
            start = content.index(f'id="{script_id}"')
            script_start = content.index(">", start) + 1
            script_end = content.index("</script>", script_start)
            payload = json.loads(content[script_start:script_end])
            self.assertIsInstance(payload, list)

        # 30 days of zero-filled revenue labels/totals, even with no orders yet.
        self.assertEqual(len(response.context["revenue_chart_labels"]), 30)
        self.assertEqual(len(response.context["revenue_chart_totals"]), 30)


class ProfitabilityReportTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@example.com", password="pw", full_name="Staff One", is_staff=True
        )
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        subcategory = Subcategory.objects.create(category=category, name="Sarees")
        self.product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("200.00"),
            cost_price=Decimal("100.00"),
            stock_count=10,
        )
        order = Order.objects.create(
            user=self.buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("200.00"),
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_title=self.product.title,
            product_sku=self.product.sku,
            unit_price=Decimal("200.00"),
            quantity=1,
            gst_rate=Decimal("0.00"),
        )
        Payment.objects.create(
            order=order,
            user=self.buyer,
            gateway_order_id="gw_1",
            amount=Decimal("200.00"),
            status=Payment.Status.CAPTURED,
        )

    def test_staff_sees_the_report(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:profitability_report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Silk Saree")
        self.assertEqual(len(response.context["rows"]), 1)

    def test_non_staff_is_redirected_away(self):
        self.client.force_login(self.buyer)

        response = self.client.get(reverse("accounts:profitability_report"))

        self.assertEqual(response.status_code, 302)

    def test_invalid_date_params_are_ignored_rather_than_erroring(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:profitability_report"), {"from": "not-a-date"})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["date_from"])

    def test_date_range_excluding_the_order_shows_no_rows(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:profitability_report"), {"from": "2099-01-01"})

        self.assertEqual(len(response.context["rows"]), 0)


def _make_taxonomy():
    department = Department.objects.create(name="Women")
    category = Category.objects.create(department=department, name="Clothing")
    return Subcategory.objects.create(category=category, name="Sarees")


class StoreDashboardProductsTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@example.com", password="pw", full_name="Staff One", is_staff=True
        )
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        subcategory = _make_taxonomy()
        for i in range(30):
            Product.objects.create(
                subcategory=subcategory,
                title=f"Product {i}",
                sku=f"SKU-{i}",
                price=Decimal("999.00"),
            )

    def test_staff_sees_paginated_products(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard_products"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 30)
        self.assertLess(len(response.context["page_obj"].object_list), 30)  # a real page, not everything at once

    def test_second_page_returns_remaining_products(self):
        self.client.force_login(self.staff)
        per_page = len(self.client.get(reverse("accounts:store_dashboard_products")).context["page_obj"].object_list)

        response = self.client.get(reverse("accounts:store_dashboard_products"), {"page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page_obj"].object_list), 30 - per_page)

    def test_non_staff_is_redirected_away(self):
        self.client.force_login(self.buyer)

        response = self.client.get(reverse("accounts:store_dashboard_products"))

        self.assertEqual(response.status_code, 302)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("accounts:store_dashboard_products"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)


class StoreDashboardOrdersTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@example.com", password="pw", full_name="Staff One", is_staff=True
        )
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        subcategory = _make_taxonomy()
        product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
        )
        self.accepted_order = self._make_order(product, status=Order.Status.ACCEPTED, suffix="A")
        self.shipped_order = self._make_order(product, status=Order.Status.SHIPPED, suffix="B")

    def _make_order(self, product, *, status, suffix):
        order = Order.objects.create(
            user=self.buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00"),
            status=status,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            product_title=f"{product.title} {suffix}",
            product_sku=product.sku,
            unit_price=Decimal("999.00"),
            quantity=1,
        )
        return order

    def test_staff_sees_all_orders_unfiltered(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard_orders"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 2)

    def test_status_filter_scopes_the_list(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard_orders"), {"status": "accepted"})

        self.assertEqual(list(response.context["page_obj"].object_list), [self.accepted_order])

    def test_invalid_status_filter_is_ignored(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard_orders"), {"status": "not-a-real-status"})

        self.assertEqual(response.context["page_obj"].paginator.count, 2)

    def test_non_staff_is_redirected_away(self):
        self.client.force_login(self.buyer)

        response = self.client.get(reverse("accounts:store_dashboard_orders"))

        self.assertEqual(response.status_code, 302)


class StoreDashboardOrderDetailTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@example.com", password="pw", full_name="Staff One", is_staff=True
        )
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        subcategory = _make_taxonomy()
        product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
        )
        self.order = Order.objects.create(
            user=self.buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00"),
            status=Order.Status.PAYMENT_CONFIRMED,
        )
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_title=product.title,
            product_sku=product.sku,
            unit_price=Decimal("999.00"),
            quantity=1,
        )

    def test_staff_sees_order_details(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard_order_detail", args=[self.order.order_number]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.order.order_number)
        self.assertContains(response, "Silk Saree")

    def test_unknown_order_number_404s(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard_order_detail", args=["ORD00000000"]))

        self.assertEqual(response.status_code, 404)

    def test_non_staff_is_redirected_away(self):
        self.client.force_login(self.buyer)

        response = self.client.get(reverse("accounts:store_dashboard_order_detail", args=[self.order.order_number]))

        self.assertEqual(response.status_code, 302)


class StoreDashboardOrderActionsTestCase(TestCase):
    """
    Every action here is deliberately checked from three angles: an
    anonymous/non-staff request must be turned away, a GET must be
    rejected (POST-only), and — the actual authorization boundary, not
    just UI convenience — an out-of-sequence transition must be rejected
    server-side even if somehow requested directly.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@example.com", password="pw", full_name="Staff One", is_staff=True
        )
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        subcategory = _make_taxonomy()
        product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
        )
        self.order = Order.objects.create(
            user=self.buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00"),
            status=Order.Status.PAYMENT_CONFIRMED,
        )
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_title=product.title,
            product_sku=product.sku,
            unit_price=Decimal("999.00"),
            quantity=1,
        )

    def test_accept_advances_status_from_payment_confirmed(self):
        self.client.force_login(self.staff)

        response = self.client.post(reverse("accounts:store_dashboard_order_accept", args=[self.order.order_number]))

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.ACCEPTED)
        self.assertRedirects(
            response, reverse("accounts:store_dashboard_order_detail", args=[self.order.order_number])
        )

    def test_accept_requires_staff(self):
        self.client.force_login(self.buyer)

        response = self.client.post(reverse("accounts:store_dashboard_order_accept", args=[self.order.order_number]))

        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAYMENT_CONFIRMED)  # untouched

    def test_accept_rejects_get(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard_order_accept", args=[self.order.order_number]))

        self.assertEqual(response.status_code, 405)

    def test_accept_rejects_wrong_prior_status_even_for_staff(self):
        # Order is already ACCEPTED — this must be rejected server-side,
        # not merely hidden by which button the template shows.
        self.order.status = Order.Status.ACCEPTED
        self.order.save()
        self.client.force_login(self.staff)

        response = self.client.post(reverse("accounts:store_dashboard_order_accept", args=[self.order.order_number]))

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.ACCEPTED)  # unchanged
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("must be" in str(m) for m in messages))

    def test_cancel_requires_a_reason(self):
        self.client.force_login(self.staff)

        response = self.client.post(reverse("accounts:store_dashboard_order_cancel", args=[self.order.order_number]))

        self.order.refresh_from_db()
        self.assertNotEqual(self.order.status, Order.Status.CANCELLED)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("reason" in str(m).lower() for m in messages))

    def test_cancel_with_reason_succeeds_and_records_it(self):
        self.client.force_login(self.staff)

        self.client.post(
            reverse("accounts:store_dashboard_order_cancel", args=[self.order.order_number]),
            {"reason": "Customer requested by phone"},
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        history = self.order.status_history.get(new_status=Order.Status.CANCELLED)
        self.assertEqual(history.reason, "Customer requested by phone")
        self.assertEqual(history.changed_by, self.staff)

    def test_next_param_redirects_back_to_a_local_url(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("accounts:store_dashboard_order_accept", args=[self.order.order_number]),
            {"next": reverse("accounts:store_dashboard_orders")},
        )

        self.assertRedirects(response, reverse("accounts:store_dashboard_orders"))

    def test_next_param_pointing_off_site_is_rejected(self):
        # Open-redirect guard: an attacker-controlled `next` must never be
        # honored, even though this is a POST-only, staff-only endpoint.
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("accounts:store_dashboard_order_accept", args=[self.order.order_number]),
            {"next": "https://evil.example.com/phish"},
        )

        self.assertRedirects(
            response, reverse("accounts:store_dashboard_order_detail", args=[self.order.order_number])
        )


class StoreDashboardRefundPaymentTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@example.com", password="pw", full_name="Staff One", is_staff=True
        )
        self.staff.user_permissions.add(Permission.objects.get(codename="change_payment"))
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        subcategory = _make_taxonomy()
        product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
        )
        self.order = Order.objects.create(
            user=self.buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00"),
            status=Order.Status.PAYMENT_CONFIRMED,
        )
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_title=product.title,
            product_sku=product.sku,
            unit_price=Decimal("999.00"),
            quantity=1,
        )
        self.payment = Payment.objects.create(
            order=self.order,
            user=self.buyer,
            gateway=Payment.Gateway.RAZORPAY,
            gateway_payment_id="pay_fake_1",
            amount=Decimal("999.00"),
            status=Payment.Status.CAPTURED,
        )
        self.fake_gateway = FakeGateway()
        patcher = mock.patch("payments.services.get_gateway", return_value=self.fake_gateway)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_full_refund_succeeds(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("accounts:store_dashboard_refund_payment", args=[self.payment.pk]),
            {"amount": "999.00", "reason": "Buyer cancelled"},
        )

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.refunded_amount, Decimal("999.00"))
        self.assertEqual(self.payment.refund_status, Payment.RefundStatus.FULL)
        self.assertRedirects(
            response, reverse("accounts:store_dashboard_order_detail", args=[self.order.order_number])
        )

    def test_partial_refund_succeeds(self):
        self.client.force_login(self.staff)

        self.client.post(
            reverse("accounts:store_dashboard_refund_payment", args=[self.payment.pk]),
            {"amount": "400.00", "reason": "Partial return"},
        )

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.refunded_amount, Decimal("400.00"))
        self.assertEqual(self.payment.refund_status, Payment.RefundStatus.PARTIAL)

    def test_amount_over_remaining_is_rejected(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("accounts:store_dashboard_refund_payment", args=[self.payment.pk]),
            {"amount": "5000.00", "reason": "Too much"},
        )

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.refunded_amount, Decimal("0.00"))
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("remaining" in str(m).lower() for m in messages))

    def test_requires_change_payment_permission(self):
        staff_without_perm = User.objects.create_user(
            email="staff2@example.com", password="pw", full_name="Staff Two", is_staff=True
        )
        self.client.force_login(staff_without_perm)

        response = self.client.post(
            reverse("accounts:store_dashboard_refund_payment", args=[self.payment.pk]),
            {"amount": "999.00", "reason": "Buyer cancelled"},
        )

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.refunded_amount, Decimal("0.00"))
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("permission" in str(m).lower() for m in messages))

    def test_rejects_get(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard_refund_payment", args=[self.payment.pk]))

        self.assertEqual(response.status_code, 405)


def _address_post_data(**overrides):
    data = {
        "label": "Home",
        "recipient_name": "Buyer One",
        "phone_number": "9876543210",
        "address_line1": "1 Street",
        "address_line2": "",
        "city": "Bengaluru",
        "state": "Karnataka",
        "postal_code": "560001",
        "country": "India",
        "is_default": "",
    }
    data.update(overrides)
    return data


class AddressBookTestCase(TestCase):
    """The saved-address book: CRUD plus the "exactly one default" invariant."""

    def setUp(self):
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        self.other_user = User.objects.create_user(email="other@example.com", password="pw", full_name="Other")
        self.client.force_login(self.buyer)

    def test_first_address_becomes_default_even_when_not_checked(self):
        self.client.post(reverse("accounts:address_add"), data=_address_post_data())

        address = Address.objects.get(user=self.buyer)
        self.assertTrue(address.is_default)

    def test_second_address_does_not_steal_default_unless_requested(self):
        self.client.post(reverse("accounts:address_add"), data=_address_post_data(label="Home"))
        self.client.post(reverse("accounts:address_add"), data=_address_post_data(label="Work"))

        addresses = {a.label: a for a in Address.objects.filter(user=self.buyer)}
        self.assertTrue(addresses["Home"].is_default)
        self.assertFalse(addresses["Work"].is_default)

    def test_setting_default_demotes_the_previous_one(self):
        self.client.post(reverse("accounts:address_add"), data=_address_post_data(label="Home"))
        self.client.post(reverse("accounts:address_add"), data=_address_post_data(label="Work"))
        work = Address.objects.get(user=self.buyer, label="Work")

        self.client.post(reverse("accounts:address_set_default", args=[work.pk]))

        addresses = {a.label: a for a in Address.objects.filter(user=self.buyer)}
        self.assertTrue(addresses["Work"].is_default)
        self.assertFalse(addresses["Home"].is_default)

    def test_editing_to_default_demotes_the_previous_one(self):
        self.client.post(reverse("accounts:address_add"), data=_address_post_data(label="Home"))
        self.client.post(reverse("accounts:address_add"), data=_address_post_data(label="Work"))
        work = Address.objects.get(user=self.buyer, label="Work")

        self.client.post(
            reverse("accounts:address_edit", args=[work.pk]),
            data=_address_post_data(label="Work", is_default="on"),
        )

        addresses = {a.label: a for a in Address.objects.filter(user=self.buyer)}
        self.assertTrue(addresses["Work"].is_default)
        self.assertFalse(addresses["Home"].is_default)

    def test_deleting_the_default_promotes_another_address(self):
        self.client.post(reverse("accounts:address_add"), data=_address_post_data(label="Home"))
        self.client.post(reverse("accounts:address_add"), data=_address_post_data(label="Work"))
        home = Address.objects.get(user=self.buyer, label="Home")

        self.client.post(reverse("accounts:address_delete", args=[home.pk]))

        remaining = Address.objects.get(user=self.buyer)
        self.assertEqual(remaining.label, "Work")
        self.assertTrue(remaining.is_default)

    def test_deleting_the_only_address_leaves_none(self):
        self.client.post(reverse("accounts:address_add"), data=_address_post_data())
        address = Address.objects.get(user=self.buyer)

        self.client.post(reverse("accounts:address_delete", args=[address.pk]))

        self.assertFalse(Address.objects.filter(user=self.buyer).exists())

    def test_address_list_only_shows_the_current_users_addresses(self):
        self.client.post(reverse("accounts:address_add"), data=_address_post_data(label="Mine"))
        Address.objects.create(
            user=self.other_user,
            recipient_name="Other",
            phone_number="9876500000",
            address_line1="2 Street",
            city="Chennai",
            state="Tamil Nadu",
            postal_code="600001",
            is_default=True,
        )

        response = self.client.get(reverse("accounts:addresses"))

        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Other")

    def test_cannot_edit_another_users_address(self):
        other_address = Address.objects.create(
            user=self.other_user,
            recipient_name="Other",
            phone_number="9876500000",
            address_line1="2 Street",
            city="Chennai",
            state="Tamil Nadu",
            postal_code="600001",
            is_default=True,
        )

        response = self.client.get(reverse("accounts:address_edit", args=[other_address.pk]))

        self.assertEqual(response.status_code, 404)

    def test_address_add_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("accounts:address_add"))

        self.assertEqual(response.status_code, 302)


class StoreDashboardAuditLogTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@example.com", password="pw", full_name="Staff One", is_staff=True
        )
        self.other_staff = User.objects.create_user(
            email="other-staff@example.com", password="pw", full_name="Other Staff", is_staff=True
        )
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        subcategory = _make_taxonomy()
        self.product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
        )
        self.product_content_type = ContentType.objects.get_for_model(Product)
        self.user_content_type = ContentType.objects.get_for_model(User)

    def _make_entry(
        self, *, actor=None, content_type=None, obj=None, action=LogEntry.Action.UPDATE, timestamp=None, changes=None
    ):
        return LogEntry.objects.create(
            actor=actor,
            content_type=content_type or self.product_content_type,
            object_pk=str(obj.pk if obj else self.product.pk),
            object_id=obj.pk if obj else self.product.pk,
            object_repr=str(obj or self.product),
            action=action,
            changes=changes or {"title": ["Old Title", "New Title"]},
            timestamp=timestamp or timezone.now(),
        )

    def test_non_staff_is_redirected_away(self):
        self.client.force_login(self.buyer)

        response = self.client.get(reverse("accounts:store_dashboard_audit_log"))

        self.assertEqual(response.status_code, 302)

    def test_staff_sees_logged_entries(self):
        self._make_entry(actor=self.staff)
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard_audit_log"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Old Title")
        self.assertContains(response, "New Title")
        self.assertContains(response, "Update")

    def test_filters_by_user(self):
        entry = self._make_entry(actor=self.staff)
        self._make_entry(actor=self.other_staff)
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard_audit_log"), {"user": self.staff.pk})

        self.assertEqual(list(response.context["page_obj"].object_list), [entry])

    def test_filters_by_model(self):
        # setUp already created self.product, which itself logged a
        # "create" entry — the model filter should keep that one too,
        # it's a genuine Product-model entry, just not the only one.
        product_entry = self._make_entry(actor=self.staff, content_type=self.product_content_type)
        user_entry = self._make_entry(actor=self.staff, content_type=self.user_content_type, obj=self.buyer)
        self.client.force_login(self.staff)

        response = self.client.get(
            reverse("accounts:store_dashboard_audit_log"), {"model": self.product_content_type.pk}
        )

        results = list(response.context["page_obj"].object_list)
        self.assertIn(product_entry, results)
        self.assertNotIn(user_entry, results)

    def test_filters_by_date_range(self):
        in_range = self._make_entry(
            actor=self.staff, timestamp=timezone.datetime(2026, 6, 15, tzinfo=timezone.get_current_timezone())
        )
        self._make_entry(
            actor=self.staff, timestamp=timezone.datetime(2026, 1, 1, tzinfo=timezone.get_current_timezone())
        )
        self.client.force_login(self.staff)

        response = self.client.get(
            reverse("accounts:store_dashboard_audit_log"),
            {"date_from": "2026-06-01", "date_to": "2026-06-30"},
        )

        self.assertEqual(list(response.context["page_obj"].object_list), [in_range])

    def test_overview_page_links_to_audit_log(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("accounts:store_dashboard"))

        self.assertContains(response, reverse("accounts:store_dashboard_audit_log"))
