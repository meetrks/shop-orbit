"""
Tests for the Razorpay checkout flow: stock validation at checkout, order
creation, and payment retry guards. Payment creation itself is faked (see
payments.tests.fakes.FakeGateway) so these never touch the network.
"""

from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Address, User
from catalog.models import Category, Department, Product, Subcategory
from fulfillment.models import PincodeServiceability
from inventory.services import reserve_stock
from payments.models import Payment
from payments.tests.fakes import FakeGateway

from .models import Cart, CartItem, Order, OrderItem, OrderStatusHistory
from .services import accept_order, cancel_order, mark_order_packed
from .tasks import cancel_stale_unpaid_orders


class AutoPromoDiscountTestCase(TestCase):
    """Buy-2-or-more-items promo — counts total quantity, not distinct products."""

    def setUp(self):
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        self.cart = Cart.objects.create(user=self.buyer)
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
        self.other_product = Product.objects.create(
            subcategory=subcategory,
            title="Cotton Saree",
            sku="SKU2",
            price=Decimal("999.00"),
            stock_count=10,
        )

    def test_no_discount_for_a_single_item(self):
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        self.assertEqual(self.cart.auto_promo_discount, Decimal("0.00"))

    def test_discount_applies_for_two_of_the_same_product(self):
        cheap_product = Product.objects.create(
            subcategory=self.product.subcategory,
            title="Bindi Pack",
            sku="SKU3",
            price=Decimal("100.00"),
            stock_count=10,
        )
        CartItem.objects.create(cart=self.cart, product=cheap_product, quantity=2)
        # A single line item, quantity 2 is enough — well under the Rs 50 cap,
        # so this actually exercises the 10% calculation rather than the cap.
        expected = (self.cart.subtotal * Decimal("0.10")).quantize(Decimal("0.01"))
        self.assertLess(expected, Decimal("50.00"))
        self.assertEqual(self.cart.auto_promo_discount, expected)

    def test_discount_applies_for_two_different_products(self):
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        CartItem.objects.create(cart=self.cart, product=self.other_product, quantity=1)
        self.assertEqual(self.cart.auto_promo_discount, Decimal("50.00"))  # capped

    def test_discount_is_capped_at_rs_50(self):
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=2)
        # 10% of Rs 1998 would be Rs 199.80 — capped at Rs 50.
        self.assertEqual(self.cart.auto_promo_discount, Decimal("50.00"))


class CheckoutTestCase(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(
            email="buyer@example.com",
            password="pw",
            full_name="Buyer One",
            phone_number="9876543210",
        )
        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        subcategory = Subcategory.objects.create(category=category, name="Sarees")
        self.product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
            stock_count=2,
            hsn_code="6204",
            gst_rate=Decimal("5.00"),
        )
        self.client.force_login(self.buyer)
        self.cart = Cart.objects.create(user=self.buyer)

        self.fake_gateway = FakeGateway()
        patcher = mock.patch("payments.services.get_gateway", return_value=self.fake_gateway)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _checkout_post_data(self):
        return {
            "shipping_full_name": "Buyer One",
            "shipping_phone_number": "9876543210",
            "shipping_address_line1": "1 Street",
            "shipping_address_line2": "",
            "shipping_city": "Bengaluru",
            "shipping_state": "Karnataka",
            "shipping_postal_code": "560001",
            "shipping_country": "India",
            "coupon_code": "",
            "customer_note": "",
        }

    def test_checkout_page_fires_begin_checkout_event(self):
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=2)

        response = self.client.get(reverse("cart:checkout"))

        self.assertContains(response, 'gtag("event", "begin_checkout"')
        self.assertContains(response, "SKU1")

    def test_checkout_blocks_submission_when_cart_exceeds_stock(self):
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=5)  # only 2 in stock

        response = self.client.post(reverse("cart:checkout"), data=self._checkout_post_data())

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())
        self.assertContains(response, "left in stock")

    def test_checkout_creates_order_and_redirects_to_payment(self):
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=2)

        response = self.client.post(reverse("cart:checkout"), data=self._checkout_post_data())

        order = Order.objects.get()
        self.assertRedirects(
            response, reverse("cart:checkout_payment", args=[order.order_number]), fetch_redirect_response=False
        )
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        # Product.display_price bundles in the default Rs 50/unit delivery
        # charge: (999.00 + 50.00) * 2.
        self.assertEqual(order.subtotal_amount, Decimal("2098.00"))
        item = order.items.get()
        self.assertEqual(item.hsn_code, "6204")
        self.assertEqual(item.gst_rate, Decimal("5.00"))
        self.assertEqual(self.cart.items.count(), 0)

        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved_count, 2)  # held for the duration of the Razorpay attempt

    def test_checkout_with_saved_address_uses_its_details(self):
        address = Address.objects.create(
            user=self.buyer,
            label="Work",
            recipient_name="Buyer One at Work",
            phone_number="9876500001",
            address_line1="42 Business Park Road",
            city="Bengaluru",
            state="Karnataka",
            postal_code="560002",
            is_default=True,
        )
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)

        response = self.client.post(
            reverse("cart:checkout"),
            data={"saved_address": address.pk, "coupon_code": "", "customer_note": ""},
        )

        order = Order.objects.get()
        self.assertRedirects(
            response, reverse("cart:checkout_payment", args=[order.order_number]), fetch_redirect_response=False
        )
        self.assertEqual(order.shipping_full_name, "Buyer One at Work")
        self.assertEqual(order.shipping_address_line1, "42 Business Park Road")
        self.assertEqual(order.shipping_postal_code, "560002")

    def test_checkout_new_address_with_save_checked_creates_saved_address(self):
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)

        self.client.post(reverse("cart:checkout"), data={**self._checkout_post_data(), "save_address": "on"})

        address = Address.objects.get(user=self.buyer)
        self.assertTrue(address.is_default)  # first saved address always becomes default
        self.assertEqual(address.recipient_name, "Buyer One")
        self.assertEqual(address.postal_code, "560001")

    def test_checkout_new_address_without_save_does_not_create_saved_address(self):
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)

        self.client.post(reverse("cart:checkout"), data=self._checkout_post_data())

        self.assertFalse(Address.objects.filter(user=self.buyer).exists())

    def test_checkout_requires_manual_fields_when_no_saved_address_chosen(self):
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)

        response = self.client.post(reverse("cart:checkout"), data={"coupon_code": "", "customer_note": ""})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())
        self.assertContains(response, "is required")

    def test_checkout_succeeds_for_a_pincode_with_no_serviceability_row(self):
        # Regression guard for the default-open design: no row at all must
        # not block checkout, since most pincodes will never have one.
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)

        response = self.client.post(reverse("cart:checkout"), data=self._checkout_post_data())

        self.assertTrue(Order.objects.exists())
        self.assertRedirects(
            response,
            reverse("cart:checkout_payment", args=[Order.objects.get().order_number]),
            fetch_redirect_response=False,
        )

    def test_checkout_blocked_for_explicitly_unserviceable_pincode(self):
        PincodeServiceability.objects.create(
            postal_code="560001", is_serviceable=False, note="Courier doesn't reach this area."
        )
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)

        response = self.client.post(reverse("cart:checkout"), data=self._checkout_post_data())

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())
        self.assertContains(response, "reach this area")

    def test_checkout_blocks_when_stock_is_already_reserved_by_another_order(self):
        # Both units of a 2-in-stock product are already held by someone
        # else's in-flight checkout (reserved, not yet paid) — this buyer's
        # checkout must be blocked even though raw stock_count alone would
        # suggest there's stock, since available-to-sell is now zero.
        other_buyer = User.objects.create_user(email="other@example.com", password="pw", full_name="Other Buyer")
        other_order = Order.objects.create(
            user=other_buyer,
            shipping_full_name="Other Buyer",
            shipping_phone_number="9876500000",
            shipping_address_line1="X",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("0.00"),
        )
        OrderItem.objects.create(
            order=other_order,
            product=self.product,
            product_title=self.product.title,
            product_sku=self.product.sku,
            unit_price=Decimal("999.00"),
            quantity=2,
        )
        reserve_stock(other_order)

        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        response = self.client.post(reverse("cart:checkout"), data=self._checkout_post_data())

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.filter(user=self.buyer).exists())
        self.assertContains(response, "left in stock")

    def test_checkout_payment_view_creates_first_payment_lazily(self):
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        self.client.post(reverse("cart:checkout"), data=self._checkout_post_data())
        order = Order.objects.get()

        response = self.client.get(reverse("cart:checkout_payment", args=[order.order_number]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Payment.objects.filter(order=order).exists())

    def test_payment_retry_rejects_already_confirmed_order(self):
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        self.client.post(reverse("cart:checkout"), data=self._checkout_post_data())
        order = Order.objects.get()
        order.status = Order.Status.PAYMENT_CONFIRMED
        order.save()

        response = self.client.post(reverse("cart:payment_retry", args=[order.order_number]))

        self.assertRedirects(response, reverse("accounts:profile"))
        self.assertFalse(Payment.objects.filter(order=order).exists())


class CancelOrderTestCase(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        self.staff = User.objects.create_user(
            email="staff@example.com", password="pw", full_name="Staff One", is_staff=True
        )
        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        subcategory = Subcategory.objects.create(category=category, name="Sarees")
        self.product = Product.objects.create(
            subcategory=subcategory,
            title="Silk Saree",
            sku="SKU1",
            price=Decimal("999.00"),
            stock_count=8,
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
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_title=self.product.title,
            product_sku=self.product.sku,
            unit_price=Decimal("999.00"),
            quantity=2,
        )

    def test_cancel_from_awaiting_payment_does_not_touch_stock(self):
        cancel_order(self.order, cancelled_by=self.buyer, reason="Changed my mind")

        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertEqual(self.product.stock_count, 8)  # untouched — nothing was ever decremented

    def test_cancel_from_payment_confirmed_restores_stock_and_notifies_staff(self):
        self.product.stock_count = 6  # simulates the 2 units already having been decremented at capture
        self.product.save()
        self.order.status = Order.Status.PAYMENT_CONFIRMED
        self.order.save()

        cancel_order(self.order, cancelled_by=self.staff, reason="Buyer requested via phone")

        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertEqual(self.product.stock_count, 8)  # restored

        subjects = [message.subject for message in mail.outbox]
        self.assertTrue(any("refund" in s.lower() for s in subjects))

    def test_cancel_from_packed_restores_real_stock_not_just_a_reservation(self):
        # Regression guard: ACCEPTED/PACKED both come after payment
        # capture, same as PAYMENT_CONFIRMED — cancelling from any of them
        # must restore real stock_count, not just release a (nonexistent)
        # checkout reservation and leave the decremented stock stranded.
        self.product.stock_count = 6  # simulates the 2 units already having been decremented at capture
        self.product.save()
        self.order.status = Order.Status.PACKED
        self.order.save()

        cancel_order(self.order, cancelled_by=self.staff, reason="Out of stock after all")

        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertEqual(self.product.stock_count, 8)  # restored, not left stranded at 6

    def test_cancel_rejects_non_cancellable_status(self):
        self.order.status = Order.Status.SHIPPED
        self.order.save()

        with self.assertRaises(ValueError):
            cancel_order(self.order)

    def test_cancel_records_status_history_with_actor_and_reason(self):
        cancel_order(self.order, cancelled_by=self.buyer, reason="Changed my mind")

        history = OrderStatusHistory.objects.get(order=self.order)
        self.assertEqual(history.previous_status, Order.Status.AWAITING_PAYMENT)
        self.assertEqual(history.new_status, Order.Status.CANCELLED)
        self.assertEqual(history.changed_by, self.buyer)
        self.assertEqual(history.reason, "Changed my mind")

    def test_buyer_can_cancel_own_order_via_view(self):
        self.client.force_login(self.buyer)

        response = self.client.post(reverse("cart:cancel_order", args=[self.order.order_number]))

        self.assertRedirects(response, reverse("cart:order_confirmation", args=[self.order.order_number]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)

    def test_buyer_cannot_cancel_someone_elses_order(self):
        other_buyer = User.objects.create_user(email="other@example.com", password="pw", full_name="Other Buyer")
        self.client.force_login(other_buyer)

        response = self.client.post(reverse("cart:cancel_order", args=[self.order.order_number]))

        self.assertEqual(response.status_code, 404)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.AWAITING_PAYMENT)


class OrderAdminChangePageTestCase(TestCase):
    """
    Regression guard: OrderItemInline's line_total display method used to
    crash on the hidden "empty form" template row Django renders for the
    inline's "Add another" JS button — that row is an unsaved OrderItem
    with unit_price/quantity both None, and unit_price * quantity raised
    a TypeError on every single order change page. Fixed by disabling add
    on the inline (order items are a frozen checkout snapshot, never
    hand-entered), which also stops that empty row from being rendered.
    """

    def test_change_page_loads_without_error(self):
        staff = User.objects.create_user(
            email="staff@example.com",
            password="pw",
            full_name="Staff One",
            is_staff=True,
            is_superuser=True,
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
        buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
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
        )
        self.client.force_login(staff)

        response = self.client.get(reverse("admin:cart_order_change", args=[order.pk]))

        self.assertEqual(response.status_code, 200)


class PurchaseEventTrackingTestCase(TestCase):
    """
    GA4's `purchase` event must fire exactly once, right when checkout
    completes — not on every later visit to the order page (e.g. from
    the profile's order history), which would inflate revenue reporting.
    """

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
            stock_count=8,
        )
        self.order = Order.objects.create(
            user=self.buyer,
            status=Order.Status.PAYMENT_CONFIRMED,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00"),
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_title=self.product.title,
            product_sku=self.product.sku,
            unit_price=Decimal("999.00"),
            quantity=1,
        )
        self.client.force_login(self.buyer)

    def test_payment_success_redirect_carries_tracking_flag(self):
        response = self.client.get(reverse("cart:payment_success", args=[self.order.order_number]))

        expected_url = reverse("cart:order_confirmation", args=[self.order.order_number]) + "?purchase_tracked=1"
        self.assertRedirects(response, expected_url)

    def test_purchase_event_fires_when_tracking_flag_present(self):
        response = self.client.get(
            reverse("cart:order_confirmation", args=[self.order.order_number]), {"purchase_tracked": "1"}
        )

        self.assertContains(response, 'gtag("event", "purchase"')
        self.assertContains(response, self.order.order_number)

    def test_purchase_event_does_not_fire_on_plain_revisit(self):
        response = self.client.get(reverse("cart:order_confirmation", args=[self.order.order_number]))

        self.assertNotContains(response, 'gtag("event", "purchase"')


class FulfillmentStageTransitionsTestCase(TestCase):
    """accept_order -> mark_order_packed: the internal, staff-driven prep stages."""

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
            stock_count=8,
        )
        self.order = Order.objects.create(
            user=buyer,
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

    def test_full_sequence_advances_status_and_records_history(self):
        accept_order(self.order, actor=self.staff)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.ACCEPTED)

        mark_order_packed(self.order, actor=self.staff)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PACKED)

        history = list(OrderStatusHistory.objects.filter(order=self.order).order_by("created_at"))
        self.assertEqual(
            [(h.previous_status, h.new_status) for h in history],
            [
                (Order.Status.PAYMENT_CONFIRMED, Order.Status.ACCEPTED),
                (Order.Status.ACCEPTED, Order.Status.PACKED),
            ],
        )
        self.assertTrue(all(h.changed_by == self.staff for h in history))

    def test_transitions_do_not_email_the_buyer(self):
        accept_order(self.order, actor=self.staff)
        self.order.refresh_from_db()
        mark_order_packed(self.order, actor=self.staff)

        # Internal staff bookkeeping stages, not customer milestones —
        # see cart.services._advance_order_status's docstring.
        self.assertEqual(len(mail.outbox), 0)

    def test_mark_packed_rejects_wrong_prior_status(self):
        # Order is PAYMENT_CONFIRMED, not ACCEPTED yet.
        with self.assertRaises(ValueError):
            mark_order_packed(self.order, actor=self.staff)

    def test_payment_confirmed_accepted_packed_are_cancellable(self):
        for status in [Order.Status.PAYMENT_CONFIRMED, Order.Status.ACCEPTED, Order.Status.PACKED]:
            self.assertIn(status, Order.CANCELLABLE_STATUSES)

    def test_paid_statuses_include_all_post_payment_stages(self):
        for status in [
            Order.Status.PAYMENT_CONFIRMED,
            Order.Status.ACCEPTED,
            Order.Status.PACKED,
            Order.Status.SHIPPED,
            Order.Status.DELIVERED,
        ]:
            self.assertIn(status, Order.PAID_STATUSES)


class OrderConfirmationRetryButtonTestCase(TestCase):
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
            stock_count=8,
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
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_title=self.product.title,
            product_sku=self.product.sku,
            unit_price=Decimal("999.00"),
            quantity=2,
        )
        self.client.force_login(self.buyer)

    def test_awaiting_payment_links_straight_to_checkout_payment(self):
        response = self.client.get(reverse("cart:order_confirmation", args=[self.order.order_number]))

        self.assertContains(response, "Retry Payment")
        self.assertContains(response, reverse("cart:checkout_payment", args=[self.order.order_number]))

    def test_payment_failed_posts_to_payment_retry(self):
        self.order.status = Order.Status.PAYMENT_FAILED
        self.order.save()

        response = self.client.get(reverse("cart:order_confirmation", args=[self.order.order_number]))

        self.assertContains(response, "Retry Payment")
        self.assertContains(response, reverse("cart:payment_retry", args=[self.order.order_number]))

    def test_payment_confirmed_shows_no_retry_button(self):
        self.order.status = Order.Status.PAYMENT_CONFIRMED
        self.order.save()

        response = self.client.get(reverse("cart:order_confirmation", args=[self.order.order_number]))

        self.assertNotContains(response, "Retry Payment")


@override_settings(ORDER_CANCEL_TIMEOUT_HOURS=24)
class CancelStaleUnpaidOrdersTestCase(TestCase):
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

    def _make_order(self, *, status, quantity=2):
        order = Order.objects.create(
            user=self.buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00") * quantity,
            status=status,
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

    def test_cancels_awaiting_payment_order_past_the_window(self):
        order = self._make_order(status=Order.Status.AWAITING_PAYMENT)
        reserve_stock(order)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timezone.timedelta(hours=25))

        cancelled_count = cancel_stale_unpaid_orders()

        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(cancelled_count, 1)
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(self.product.reserved_count, 0)

    def test_cancels_payment_failed_order_past_the_window(self):
        # A failed payment already released its reservation (see
        # payments.pipeline.on_payment_failed) — nothing left to reserve here,
        # this only exercises the status transition.
        order = self._make_order(status=Order.Status.PAYMENT_FAILED)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timezone.timedelta(hours=25))

        cancelled_count = cancel_stale_unpaid_orders()

        order.refresh_from_db()
        self.assertEqual(cancelled_count, 1)
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_leaves_recent_unpaid_orders_alone(self):
        order = self._make_order(status=Order.Status.AWAITING_PAYMENT)
        reserve_stock(order)
        # created_at defaults to now — well within the 24-hour window.

        cancelled_count = cancel_stale_unpaid_orders()

        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(cancelled_count, 0)
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.assertEqual(self.product.reserved_count, 2)

    def test_leaves_paid_orders_alone(self):
        order = self._make_order(status=Order.Status.PAYMENT_CONFIRMED)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timezone.timedelta(hours=25))

        cancelled_count = cancel_stale_unpaid_orders()

        order.refresh_from_db()
        self.assertEqual(cancelled_count, 0)
        self.assertEqual(order.status, Order.Status.PAYMENT_CONFIRMED)

    def test_leaves_already_expired_orders_alone(self):
        # EXPIRED isn't in CANCELLABLE_STATUSES — its reservation is
        # already gone and it's already a terminal, buyer-visible dead end,
        # so there's nothing left for this sweep to do.
        order = self._make_order(status=Order.Status.EXPIRED)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timezone.timedelta(hours=25))

        cancelled_count = cancel_stale_unpaid_orders()

        order.refresh_from_db()
        self.assertEqual(cancelled_count, 0)
        self.assertEqual(order.status, Order.Status.EXPIRED)
        for status in [Order.Status.AWAITING_PAYMENT, Order.Status.PAYMENT_FAILED, Order.Status.CANCELLED]:
            self.assertNotIn(status, Order.PAID_STATUSES)


class OrderNumberGenerationTestCase(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")

    def _make_order(self):
        return Order.objects.create(
            user=self.buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=Decimal("999.00"),
        )

    @override_settings(ORDER_NUMBER_PREFIX="ORD")
    def test_defaults_to_ord_prefix(self):
        order = self._make_order()
        self.assertTrue(order.order_number.startswith("ORD"))

    @override_settings(ORDER_NUMBER_PREFIX="XYZ")
    def test_prefix_is_configurable(self):
        order = self._make_order()
        self.assertTrue(order.order_number.startswith("XYZ"))
