"""Tests for the static/legal pages, sitemap, robots.txt, and custom error pages."""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from catalog.icons import get_category_icon
from catalog.models import Category, Department, Product, Review, Subcategory
from pages.models import HomeBanner, HomePriceTier, HomeTestimonialSection, HomeTestimonialSectionReview


class PolicyPagesTestCase(TestCase):
    def test_all_policy_pages_load(self):
        for url_name in [
            "pages:privacy_policy",
            "pages:terms_conditions",
            "pages:shipping_policy",
            "pages:return_refund_policy",
            "pages:cancellation_policy",
        ]:
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200, url_name)

    def test_return_refund_policy_shows_configured_return_window(self):
        response = self.client.get(reverse("pages:return_refund_policy"))
        self.assertContains(response, "7 days")

    def test_grievance_section_hidden_when_not_configured(self):
        response = self.client.get(reverse("pages:return_refund_policy"))
        self.assertNotContains(response, "Grievance Officer")

    @override_settings(GRIEVANCE_OFFICER_NAME="Jane Doe", GRIEVANCE_OFFICER_EMAIL="grievance@example.com")
    def test_grievance_section_shown_when_configured(self):
        response = self.client.get(reverse("pages:return_refund_policy"))
        self.assertContains(response, "Grievance Officer")
        self.assertContains(response, "Jane Doe")

    def test_footer_links_to_new_policy_pages(self):
        response = self.client.get(reverse("pages:home"))
        for url_name in ["pages:shipping_policy", "pages:return_refund_policy", "pages:cancellation_policy"]:
            self.assertContains(response, reverse(url_name))


class RobotsTxtTestCase(TestCase):
    def test_robots_txt_served_at_root(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")

    def test_every_login_gated_url_prefix_is_disallowed(self):
        # Regression guard: /profile/ and /store-dashboard/ are nested
        # under the site root by accounts.urls, not under /accounts/, so
        # "Disallow: /accounts/" alone doesn't cover them — this caught a
        # real gap once already. payments/fulfillment/returns are entirely
        # auth-gated apps with no public pages, so they belong here too.
        response = self.client.get("/robots.txt")
        content = response.content.decode()
        for prefix in [
            "/admin/",
            "/accounts/",
            "/profile/",
            "/store-dashboard/",
            "/cart/",
            "/payments/",
            "/fulfillment/",
            "/returns/",
            "/picweight/",
        ]:
            self.assertIn(f"Disallow: {prefix}", content, prefix)


class PwaTestCase(TestCase):
    def test_manifest_served_at_root_with_correct_content_type(self):
        response = self.client.get("/manifest.json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")

    @override_settings(SITE_NAME="Shop Orbit")
    def test_manifest_reflects_configured_site_name(self):
        response = self.client.get("/manifest.json")

        data = response.json()
        self.assertEqual(data["name"], "Shop Orbit")
        self.assertEqual(data["short_name"], "Shop Orbit")
        self.assertEqual(data["start_url"], "/")
        self.assertEqual(len(data["icons"]), 3)
        self.assertTrue(any(icon["purpose"] == "maskable" for icon in data["icons"]))

    @override_settings(SITE_NAME="Test Store")
    def test_manifest_is_reskinned_via_settings(self):
        response = self.client.get("/manifest.json")

        data = response.json()
        self.assertEqual(data["name"], "Test Store")

    def test_service_worker_served_at_root_with_correct_content_type(self):
        response = self.client.get("/sw.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertContains(response, "self.addEventListener")

    def test_base_template_links_manifest_and_registers_service_worker(self):
        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, reverse("pages:manifest"))
        self.assertContains(response, reverse("pages:service_worker"))


class HomeHeroBannerTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="buyer@example.com", password="pw", full_name="Buyer One")

    def test_create_account_shown_to_anonymous_visitor_with_no_banners(self):
        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Create an Account")

    def test_create_account_hidden_from_logged_in_user_with_no_banners(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Create an Account")

    def test_create_account_hidden_from_logged_in_user_with_a_banner(self):
        HomeBanner.objects.create(heading="Welcome", is_active=True)
        self.client.force_login(self.user)

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Create an Account")


class SitemapTestCase(TestCase):
    def test_sitemap_loads_and_is_xml(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")
        self.assertContains(response, "<urlset")

    def test_sitemap_includes_static_pages(self):
        response = self.client.get("/sitemap.xml")
        content = response.content.decode()
        self.assertIn(reverse("pages:home"), content)
        self.assertIn(reverse("pages:shipping_policy"), content)


@override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
class ErrorPagesTestCase(TestCase):
    def test_404_renders_custom_template(self):
        response = self.client.get("/this-page-does-not-exist/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "We couldn't find that page", status_code=404)


class HomeTestimonialSectionTestCase(TestCase):
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
            is_active=True,
        )

    def _make_review(self, *, rating, comment="Lovely product!", is_verified_purchase=True, user=None):
        return Review.objects.create(
            product=self.product,
            user=user or self.buyer,
            rating=rating,
            comment=comment,
            is_verified_purchase=is_verified_purchase,
        )

    def test_latest_source_shows_qualifying_reviews(self):
        review = self._make_review(rating=5)
        HomeTestimonialSection.objects.create(
            title="What Our Customers Say",
            source=HomeTestimonialSection.Source.LATEST,
            minimum_rating=4,
            is_active=True,
        )

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "What Our Customers Say")
        self.assertContains(response, review.comment)

    def test_latest_source_excludes_reviews_below_minimum_rating(self):
        self._make_review(rating=2, comment="It was okay.")
        HomeTestimonialSection.objects.create(
            title="What Our Customers Say",
            source=HomeTestimonialSection.Source.LATEST,
            minimum_rating=4,
            is_active=True,
        )

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "It was okay.")

    def test_latest_source_excludes_reviews_without_written_feedback(self):
        # A bare star rating with no comment isn't a quotable testimonial.
        self._make_review(rating=5, comment="")
        HomeTestimonialSection.objects.create(
            title="What Our Customers Say",
            source=HomeTestimonialSection.Source.LATEST,
            minimum_rating=4,
            is_active=True,
        )

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "What Our Customers Say")

    def test_latest_source_excludes_unverified_purchases(self):
        self._make_review(rating=5, comment="Great!", is_verified_purchase=False)
        HomeTestimonialSection.objects.create(
            title="What Our Customers Say",
            source=HomeTestimonialSection.Source.LATEST,
            minimum_rating=4,
            is_active=True,
        )

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Great!")

    def test_manual_source_only_shows_picked_reviews(self):
        featured = self._make_review(rating=5, comment="Absolutely stunning craftsmanship.")
        other_buyer = User.objects.create_user(email="other@example.com", password="pw", full_name="Other Buyer")
        self._make_review(rating=5, comment="Not featured but also great.", user=other_buyer)
        section = HomeTestimonialSection.objects.create(
            title="Featured Reviews",
            source=HomeTestimonialSection.Source.MANUAL,
            is_active=True,
        )
        HomeTestimonialSectionReview.objects.create(section=section, review=featured)

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Absolutely stunning craftsmanship.")
        self.assertNotContains(response, "Not featured but also great.")

    def test_reviews_are_ordered_highest_rating_first(self):
        other_buyer = User.objects.create_user(email="other@example.com", password="pw", full_name="Other Buyer")
        self._make_review(rating=4, comment="Pretty good.")
        self._make_review(rating=5, comment="Outstanding!", user=other_buyer)
        HomeTestimonialSection.objects.create(
            title="What Our Customers Say",
            source=HomeTestimonialSection.Source.LATEST,
            minimum_rating=4,
            is_active=True,
        )

        response = self.client.get(reverse("pages:home"))
        content = response.content.decode()

        self.assertLess(content.index("Outstanding!"), content.index("Pretty good."))

    def test_inactive_section_is_not_shown(self):
        review = self._make_review(rating=5, comment="Should stay hidden.")
        HomeTestimonialSection.objects.create(
            title="Hidden Section",
            source=HomeTestimonialSection.Source.LATEST,
            minimum_rating=4,
            is_active=False,
        )

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, review.comment)

    def test_section_with_no_qualifying_reviews_is_not_shown(self):
        HomeTestimonialSection.objects.create(
            title="Empty Section",
            source=HomeTestimonialSection.Source.LATEST,
            minimum_rating=4,
            is_active=True,
        )

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Empty Section")


class HomeShopByCategoryTestCase(TestCase):
    def setUp(self):
        department = Department.objects.create(name="Women")
        self.category = Category.objects.create(department=department, name="Clothing")

    def test_subcategory_appears_with_link(self):
        subcategory = Subcategory.objects.create(category=self.category, name="Sarees")

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Shop by Category")
        self.assertContains(response, "Sarees")
        self.assertContains(response, subcategory.get_absolute_url())

    def test_falls_back_to_a_matched_svg_icon_when_no_icon_uploaded(self):
        Subcategory.objects.create(category=self.category, name="Sarees")

        response = self.client.get(reverse("pages:home"))

        # No icon uploaded — falls back to a colored SVG (see
        # catalog.icons.get_category_icon), not a broken <img>.
        self.assertNotContains(response, "<img")
        self.assertContains(response, "<svg")

    def test_uses_a_generic_icon_for_an_unmatched_category_name(self):
        Subcategory.objects.create(category=self.category, name="Something Unusual")

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, get_category_icon("Something Unusual"))

    def test_section_hidden_when_no_subcategories(self):
        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Shop by Category")

    def test_carousel_arrows_hidden_at_five_or_fewer(self):
        for i in range(5):
            Subcategory.objects.create(category=self.category, name=f"Sub {i}")

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Previous category")
        self.assertNotContains(response, "Next category")

    def test_carousel_arrows_shown_beyond_five(self):
        for i in range(6):
            Subcategory.objects.create(category=self.category, name=f"Sub {i}")

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Previous category")
        self.assertContains(response, "Next category")


class HomeShopByPriceTestCase(TestCase):
    def test_active_tier_shows_as_a_max_price_filter_link(self):
        HomePriceTier.objects.create(label="Under Rs 99", max_price=Decimal("99.00"), is_active=True)

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Shop by Price")
        self.assertContains(response, "Under Rs 99")
        self.assertContains(response, "?max_price=99.00")

    def test_inactive_tier_is_not_shown(self):
        HomePriceTier.objects.create(label="Under Rs 99", max_price=Decimal("99.00"), is_active=False)

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Shop by Price")

    def test_tiers_render_in_display_order(self):
        HomePriceTier.objects.create(label="Under Rs 199", max_price=Decimal("199.00"), display_order=2)
        HomePriceTier.objects.create(label="Under Rs 99", max_price=Decimal("99.00"), display_order=1)

        response = self.client.get(reverse("pages:home"))
        content = response.content.decode()

        self.assertLess(content.index("Under Rs 99"), content.index("Under Rs 199"))
