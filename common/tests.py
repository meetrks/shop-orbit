"""Tests for common.emails.send_templated_email and common.audit's audit-log registration."""

from decimal import Decimal

from auditlog.models import LogEntry
from django.core import mail
from django.test import TestCase, override_settings

from accounts.models import User
from cart.models import Cart
from catalog.models import Category, Department, Product, Subcategory

from .emails import send_templated_email


class SendTemplatedEmailReplyToTestCase(TestCase):
    @override_settings(REPLY_TO_EMAIL="support@example.com")
    def test_defaults_to_reply_to_email_setting(self):
        send_templated_email("welcome", {"user": None}, to="buyer@example.com")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].reply_to, ["support@example.com"])

    @override_settings(REPLY_TO_EMAIL="")
    def test_omits_header_when_reply_to_email_is_blank(self):
        send_templated_email("welcome", {"user": None}, to="buyer@example.com")

        self.assertEqual(mail.outbox[0].reply_to, [])

    def test_explicit_reply_to_overrides_default(self):
        send_templated_email("welcome", {"user": None}, to="buyer@example.com", reply_to="orders@example.com")

        self.assertEqual(mail.outbox[0].reply_to, ["orders@example.com"])


class AuditLogRegistrationTestCase(TestCase):
    """
    Sanity check for common.audit's registration list — not exhaustive
    (that's what accounts.tests.StoreDashboardAuditLogTestCase covers for
    the dashboard page itself), just a canary that registration actually
    wires up and behaves as documented.
    """

    def setUp(self):
        department = Department.objects.create(name="Women")
        category = Category.objects.create(department=department, name="Clothing")
        self.subcategory = Subcategory.objects.create(category=category, name="Sarees")

    def test_saving_a_registered_model_creates_a_log_entry(self):
        product = Product.objects.create(
            subcategory=self.subcategory, title="Silk Saree", sku="SKU1", price=Decimal("999.00")
        )

        entry = LogEntry.objects.get(
            content_type__model="product", object_pk=str(product.pk), action=LogEntry.Action.CREATE
        )
        self.assertEqual(entry.object_repr, str(product))

    def test_timestamp_fields_are_excluded_from_the_diff(self):
        product = Product.objects.create(
            subcategory=self.subcategory, title="Silk Saree", sku="SKU1", price=Decimal("999.00")
        )
        product.title = "Updated Silk Saree"
        product.save()

        entry = LogEntry.objects.get(
            content_type__model="product", object_pk=str(product.pk), action=LogEntry.Action.UPDATE
        )
        self.assertIn("title", entry.changes)
        self.assertNotIn("created_at", entry.changes)
        self.assertNotIn("updated_at", entry.changes)

    def test_password_is_masked_not_shown_in_the_diff(self):
        user = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        user.set_password("a-new-password")
        user.save()

        entry = LogEntry.objects.get(content_type__model="user", object_pk=str(user.pk), action=LogEntry.Action.UPDATE)
        self.assertIn("password", entry.changes)
        self.assertNotIn("a-new-password", str(entry.changes))

    def test_ephemeral_cart_is_not_registered(self):
        user = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        cart = Cart.objects.create(user=user)

        self.assertFalse(LogEntry.objects.filter(content_type__model="cart", object_pk=str(cart.pk)).exists())

    def test_reverse_relations_are_not_diffed_as_fields(self):
        # Regression guard: django-auditlog's own field selection only
        # skips many-to-many fields, not reverse relations — Product has
        # a dozen of them (reviews, order_items, variants, ...), and
        # diffing those produced garbage like {"reviews": ["catalog
        # .Review.None", "None"]} on every create/delete. See
        # common.audit._reverse_relation_names.
        product = Product.objects.create(
            subcategory=self.subcategory, title="Silk Saree", sku="SKU1", price=Decimal("999.00")
        )

        entry = LogEntry.objects.get(
            content_type__model="product", object_pk=str(product.pk), action=LogEntry.Action.CREATE
        )
        for reverse_field in ["reviews", "variants", "order_items", "cart_items", "eav_values"]:
            self.assertNotIn(reverse_field, entry.changes)
        self.assertIn("title", entry.changes)
