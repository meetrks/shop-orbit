"""
Tests for Phase 4 advanced search: relevance ranking, typo tolerance via
trigram similarity, SKU matching, taxonomy-name matching, per-view search
scoping, and the empty-results fallback. `Product.search_vector` is kept
up to date by a signal (see catalog.signals), so it's ready by the time
these tests query it — no manual reindexing needed.
"""

import datetime
import json
import re
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from cart.models import Order, OrderItem
from payments.models import Payment, Refund

from .models import Category, Department, Product, Subcategory
from .profitability import product_profitability_report
from .search import search_products


def _make_taxonomy(department="Women", category="Clothing", subcategory="Sarees"):
    department = Department.objects.create(name=department)
    category = Category.objects.create(department=department, name=category)
    subcategory = Subcategory.objects.create(category=category, name=subcategory)
    return department, category, subcategory


class SearchProductsTestCase(TestCase):
    def setUp(self):
        self.department, self.category, self.subcategory = _make_taxonomy()

    def _make_product(self, title, *, sku, description="", subcategory=None, stock_count=10):
        return Product.objects.create(
            subcategory=subcategory or self.subcategory,
            title=title,
            sku=sku,
            description=description,
            price=Decimal("999.00"),
            stock_count=stock_count,
        )

    def test_title_match_ranks_above_description_only_match(self):
        title_match = self._make_product("Silk Saree", sku="SKU-TITLE")
        description_match = self._make_product(
            "Cotton Kurta", sku="SKU-DESC", description="Pairs well with a silk saree."
        )

        results = list(search_products(Product.objects.all(), "saree"))

        self.assertEqual(results[0].pk, title_match.pk)
        self.assertIn(description_match.pk, [p.pk for p in results])

    def test_typo_tolerance_via_trigram_similarity(self):
        product = self._make_product("Silk Saree", sku="SKU-TYPO")

        results = list(search_products(Product.objects.all(), "saaree"))

        self.assertIn(product.pk, [p.pk for p in results])

    def test_sku_exact_match(self):
        product = self._make_product("Blue Handbag", sku="HB-12345")

        results = list(search_products(Product.objects.all(), "HB-12345"))

        self.assertEqual([p.pk for p in results], [product.pk])

    def test_taxonomy_name_match_surfaces_products_without_literal_title_match(self):
        product = self._make_product("Zari Border Six Yards", sku="SKU-TAXO")

        results = list(search_products(Product.objects.all(), "sarees"))

        self.assertIn(product.pk, [p.pk for p in results])

    def test_unrelated_query_returns_no_results(self):
        self._make_product("Silk Saree", sku="SKU-UNRELATED")

        results = list(search_products(Product.objects.all(), "xyznonexistentquery"))

        self.assertEqual(results, [])


class CatalogSearchViewTestCase(TestCase):
    def setUp(self):
        self.department, self.category, self.subcategory = _make_taxonomy()
        self.other_department, self.other_category, self.other_subcategory = _make_taxonomy(
            department="Men", category="Footwear", subcategory="Sneakers"
        )

    def _make_product(self, title, *, sku, subcategory, price=Decimal("999.00")):
        return Product.objects.create(
            subcategory=subcategory,
            title=title,
            sku=sku,
            price=price,
            stock_count=10,
        )

    def test_product_list_search_returns_matching_product(self):
        match = self._make_product("Silk Saree", sku="SKU-A", subcategory=self.subcategory)
        self._make_product("Leather Sneakers", sku="SKU-B", subcategory=self.other_subcategory)

        response = self.client.get(reverse("catalog:product_list"), {"q": "saree"})

        self.assertContains(response, match.title)
        self.assertNotContains(response, "Leather Sneakers")

    def test_department_detail_scopes_search_to_department(self):
        # Same search term matches products in both departments, but the
        # department page should only surface its own.
        in_scope = self._make_product("Sarees Silk Special", sku="SKU-C", subcategory=self.subcategory)
        out_of_scope = Product.objects.create(
            subcategory=self.other_subcategory,
            title="Sarees Sneakers Combo",
            sku="SKU-D",
            price=Decimal("999.00"),
            stock_count=10,
        )

        response = self.client.get(reverse("catalog:department_detail", args=[self.department.slug]), {"q": "sarees"})

        self.assertContains(response, in_scope.title)
        self.assertNotContains(response, out_of_scope.title)

    def test_explicit_sort_overrides_relevance_when_query_present(self):
        cheap = self._make_product("Saree Budget", sku="SKU-E", subcategory=self.subcategory, price=Decimal("500.00"))
        expensive = self._make_product(
            "Saree Premium", sku="SKU-F", subcategory=self.subcategory, price=Decimal("5000.00")
        )

        response = self.client.get(reverse("catalog:product_list"), {"q": "saree", "sort": "price_asc"})

        titles_in_order = [p.title for p in response.context["page_obj"]]
        self.assertEqual(titles_in_order, [cheap.title, expensive.title])

    def test_empty_search_result_shows_fallback_products(self):
        fallback_candidate = self._make_product("Silk Saree", sku="SKU-G", subcategory=self.subcategory)

        response = self.client.get(reverse("catalog:product_list"), {"q": "xyznonexistentquery"})

        self.assertContains(response, "You might like")
        self.assertContains(response, fallback_candidate.title)

    def test_no_query_does_not_show_fallback_products(self):
        self._make_product("Silk Saree", sku="SKU-H", subcategory=self.subcategory)

        response = self.client.get(reverse("catalog:product_list"))

        self.assertNotContains(response, "You might like")


class ProductStructuredDataTestCase(TestCase):
    """Product/Offer and BreadcrumbList JSON-LD on the product detail page."""

    def setUp(self):
        self.department, self.category, self.subcategory = _make_taxonomy()
        self.product = Product.objects.create(
            subcategory=self.subcategory,
            title="Silk Saree",
            sku="SKU-JSONLD",
            description="A fine silk saree.",
            price=Decimal("999.00"),
            stock_count=10,
        )

    def _json_ld_blocks(self, response):
        content = response.content.decode()
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', content, re.DOTALL)
        return [json.loads(block) for block in blocks]

    def test_product_json_ld_present_and_valid(self):
        response = self.client.get(reverse("catalog:product_detail", kwargs={"product_slug": self.product.slug}))
        blocks = self._json_ld_blocks(response)
        types = {block["@type"] for block in blocks}
        self.assertIn("Product", types)
        self.assertIn("BreadcrumbList", types)

        product_block = next(b for b in blocks if b["@type"] == "Product")
        self.assertEqual(product_block["sku"], "SKU-JSONLD")
        self.assertEqual(product_block["offers"]["availability"], "https://schema.org/InStock")
        self.assertNotIn("aggregateRating", product_block)

    def test_out_of_stock_product_marked_unavailable(self):
        self.product.stock_count = 0
        self.product.save()

        response = self.client.get(reverse("catalog:product_detail", kwargs={"product_slug": self.product.slug}))
        blocks = self._json_ld_blocks(response)
        product_block = next(b for b in blocks if b["@type"] == "Product")
        self.assertEqual(product_block["offers"]["availability"], "https://schema.org/OutOfStock")

    def test_breadcrumb_list_has_five_positions(self):
        response = self.client.get(reverse("catalog:product_detail", kwargs={"product_slug": self.product.slug}))
        blocks = self._json_ld_blocks(response)
        breadcrumb_block = next(b for b in blocks if b["@type"] == "BreadcrumbList")
        self.assertEqual(len(breadcrumb_block["itemListElement"]), 5)
        self.assertEqual(breadcrumb_block["itemListElement"][-1]["name"], "Silk Saree")


class ProductAnalyticsTrackingTestCase(TestCase):
    """GA4 view_item on load, and the add-to-cart form carries the data attributes analytics.js reads."""

    def setUp(self):
        self.user = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        self.department, self.category, self.subcategory = _make_taxonomy()
        self.product = Product.objects.create(
            subcategory=self.subcategory,
            title="Silk Saree",
            sku="SKU-GA4",
            price=Decimal("999.00"),
            stock_count=10,
        )

    def test_view_item_event_fires_with_product_details(self):
        response = self.client.get(reverse("catalog:product_detail", kwargs={"product_slug": self.product.slug}))
        self.assertContains(response, 'gtag("event", "view_item"')
        self.assertContains(response, "SKU-GA4")

    def test_add_to_cart_form_carries_tracking_data_attributes(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("catalog:product_detail", kwargs={"product_slug": self.product.slug}))
        self.assertContains(response, "js-track-add-to-cart")
        self.assertContains(response, 'data-item-id="SKU-GA4"')


@override_settings(
    PAYMENT_GATEWAY_FEE_PERCENT=2.0,
    DEFAULT_SHIPPING_COST_PER_ORDER=Decimal("20.00"),
    DEFAULT_PACKAGING_COST_PER_UNIT=Decimal("5.00"),
)
class ProductProfitabilityReportTestCase(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")
        self.department, self.category, self.subcategory = _make_taxonomy()

    def _paid_order(self, *, subtotal, discount=Decimal("0.00")):
        order = Order.objects.create(
            user=self.buyer,
            shipping_full_name="Buyer One",
            shipping_phone_number="9876543210",
            shipping_address_line1="1 Street",
            shipping_city="Bengaluru",
            shipping_state="Karnataka",
            shipping_postal_code="560001",
            subtotal_amount=subtotal,
            discount_amount=discount,
        )
        Payment.objects.create(
            order=order,
            user=self.buyer,
            gateway_order_id=f"gw_{order.pk}",
            amount=subtotal - discount,
            status=Payment.Status.CAPTURED,
        )
        return order

    def test_single_product_profitability_math(self):
        # No GST (gst_rate=0), so taxable_value == line_total == unit_price * quantity.
        product = self._make_product(
            "Silk Saree",
            sku="SKU-COST",
            price=Decimal("200.00"),
            cost_price=Decimal("100.00"),
        )
        order = self._paid_order(subtotal=Decimal("400.00"))
        OrderItem.objects.create(
            order=order,
            product=product,
            product_title=product.title,
            product_sku=product.sku,
            unit_price=Decimal("200.00"),
            quantity=2,
            gst_rate=Decimal("0.00"),
        )

        [row] = product_profitability_report()

        self.assertEqual(row["units_sold"], 2)
        self.assertEqual(row["revenue"], Decimal("400.00"))
        self.assertEqual(row["cogs"], Decimal("200.00"))
        self.assertEqual(row["gross_margin"], Decimal("200.00"))
        self.assertEqual(row["gross_margin_percent"], Decimal("50.0"))
        self.assertEqual(row["gateway_cost"], Decimal("8.00"))  # 400 * 2%
        self.assertEqual(row["shipping_cost"], Decimal("20.00"))  # only item in its order -> full share
        self.assertEqual(row["packaging_cost"], Decimal("10.00"))  # 5.00 * 2 units
        self.assertEqual(row["refund_cost"], Decimal("0.00"))
        self.assertEqual(row["net_contribution"], Decimal("162.00"))  # 200 - 8 - 20 - 10
        self.assertFalse(row["cost_price_missing"])

    def test_missing_cost_price_is_flagged(self):
        product = self._make_product("Silk Saree", sku="SKU-NOCOST", price=Decimal("200.00"))
        order = self._paid_order(subtotal=Decimal("200.00"))
        OrderItem.objects.create(
            order=order,
            product=product,
            product_title=product.title,
            product_sku=product.sku,
            unit_price=Decimal("200.00"),
            quantity=1,
            gst_rate=Decimal("0.00"),
        )

        [row] = product_profitability_report()

        self.assertTrue(row["cost_price_missing"])
        self.assertEqual(row["cogs"], Decimal("0.00"))

    def test_processed_refund_reduces_net_contribution(self):
        product = self._make_product(
            "Silk Saree",
            sku="SKU-REFUND",
            price=Decimal("200.00"),
            cost_price=Decimal("100.00"),
        )
        order = self._paid_order(subtotal=Decimal("200.00"))
        OrderItem.objects.create(
            order=order,
            product=product,
            product_title=product.title,
            product_sku=product.sku,
            unit_price=Decimal("200.00"),
            quantity=1,
            gst_rate=Decimal("0.00"),
        )
        payment = order.payments.get()
        Refund.objects.create(payment=payment, amount=Decimal("200.00"), status=Refund.Status.PROCESSED)
        Refund.objects.create(
            payment=payment, amount=Decimal("50.00"), status=Refund.Status.INITIATED
        )  # not counted yet

        [row] = product_profitability_report()

        self.assertEqual(row["refund_cost"], Decimal("200.00"))

    def test_unpaid_order_is_excluded(self):
        product = self._make_product("Silk Saree", sku="SKU-UNPAID", price=Decimal("200.00"))
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
            product=product,
            product_title=product.title,
            product_sku=product.sku,
            unit_price=Decimal("200.00"),
            quantity=1,
            gst_rate=Decimal("0.00"),
        )
        # No captured Payment for this order at all.

        self.assertEqual(product_profitability_report(), [])

    def test_date_range_filters_to_orders_in_range(self):
        product = self._make_product("Silk Saree", sku="SKU-DATE", price=Decimal("200.00"))
        order = self._paid_order(subtotal=Decimal("200.00"))
        OrderItem.objects.create(
            order=order,
            product=product,
            product_title=product.title,
            product_sku=product.sku,
            unit_price=Decimal("200.00"),
            quantity=1,
            gst_rate=Decimal("0.00"),
        )
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)

        self.assertEqual(len(product_profitability_report(date_from=tomorrow)), 0)
        self.assertEqual(len(product_profitability_report(date_from=yesterday, date_to=tomorrow)), 1)

    def _make_product(self, title, *, sku, price, cost_price=Decimal("0.00")):
        return Product.objects.create(
            subcategory=self.subcategory,
            title=title,
            sku=sku,
            price=price,
            cost_price=cost_price,
            stock_count=10,
        )
