"""Tests for the static/legal pages, sitemap, robots.txt, and custom error pages."""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from catalog.icons import get_category_icon
from catalog.models import Category, Department, Product, Review, Subcategory
from pages.icons import get_named_icon
from pages.models import (
    HomeBanner,
    HomeCategorySpotlightSection,
    HomeCategorySpotlightTile,
    HomeGalleryItem,
    HomeGallerySection,
    HomeLifestyleSection,
    HomeLifestyleTile,
    HomeLovedByQuote,
    HomeLovedBySection,
    HomePriceTier,
    HomePromoBanner,
    HomeTestimonialSection,
    HomeTestimonialSectionReview,
    HomeTrustStripItem,
    HomeTrustStripSection,
    HomeValuePropItem,
    HomeValuePropSection,
)


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

    @override_settings(SITE_VERSION="20260815143022")
    def test_service_worker_cache_name_reflects_site_version(self):
        # Regression guard: the cache name must change with SITE_VERSION,
        # or a deploy can never bust returning visitors' stale static-asset
        # cache (see templates/sw.js's activate handler, which only clears
        # caches whose name doesn't match the current CACHE_NAME).
        response = self.client.get("/sw.js")

        self.assertContains(response, 'const CACHE_NAME = "avr-static-20260815143022";')

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


class HomeBannerPlacementTestCase(TestCase):
    def test_hero_banner_shown_at_top_not_in_closing_slot(self):
        HomeBanner.objects.create(heading="Top of the page", placement=HomeBanner.Placement.HERO, is_active=True)

        response = self.client.get(reverse("pages:home"))
        content = response.content.decode()

        self.assertIn("Top of the page", content)
        # The hero heading renders as an <h1>; a closing-slot banner would
        # render as an <h2> in the dark bg-navy-900 section instead.
        self.assertIn(
            '<h1 class="font-serif text-4xl sm:text-5xl text-navy-900 leading-tight mb-4">Top of the page</h1>',
            content,
        )

    def test_closing_banner_shown_above_footer_not_in_hero_slot(self):
        HomeBanner.objects.create(heading="One last look", placement=HomeBanner.Placement.CLOSING, is_active=True)

        response = self.client.get(reverse("pages:home"))
        content = response.content.decode()

        self.assertIn("One last look", content)
        self.assertNotIn(
            '<h1 class="font-serif text-4xl sm:text-5xl text-navy-900 leading-tight mb-4">One last look</h1>',
            content,
        )

    def test_default_placement_is_hero(self):
        banner = HomeBanner.objects.create(heading="Untouched")

        self.assertEqual(banner.placement, HomeBanner.Placement.HERO)


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


class HomeLifestyleSectionTestCase(TestCase):
    def test_renders_when_active_with_a_tile(self):
        section = HomeLifestyleSection.objects.create(title="Shop Your Look", is_active=True)
        HomeLifestyleTile.objects.create(section=section, label="For Everyday")

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Shop Your Look")
        self.assertContains(response, "For Everyday")

    def test_hidden_when_inactive(self):
        section = HomeLifestyleSection.objects.create(title="Shop Your Look", is_active=False)
        HomeLifestyleTile.objects.create(section=section, label="For Everyday")

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Shop Your Look")

    def test_hidden_when_active_with_no_tiles(self):
        HomeLifestyleSection.objects.create(title="Shop Your Look", is_active=True)

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Shop Your Look")

    def test_tiles_render_in_display_order(self):
        section = HomeLifestyleSection.objects.create(title="Shop Your Look", is_active=True)
        HomeLifestyleTile.objects.create(section=section, label="For Weddings", display_order=2)
        HomeLifestyleTile.objects.create(section=section, label="For Everyday", display_order=1)

        response = self.client.get(reverse("pages:home"))
        content = response.content.decode()

        self.assertLess(content.index("For Everyday"), content.index("For Weddings"))

    def test_only_the_first_active_section_is_shown(self):
        older = HomeLifestyleSection.objects.create(title="Older Look", is_active=True, display_order=1)
        HomeLifestyleTile.objects.create(section=older, label="Older Tile")
        newer = HomeLifestyleSection.objects.create(title="Newer Look", is_active=True, display_order=2)
        HomeLifestyleTile.objects.create(section=newer, label="Newer Tile")

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Older Look")
        self.assertNotContains(response, "Newer Look")

    def test_carousel_arrows_hidden_at_four_or_fewer(self):
        section = HomeLifestyleSection.objects.create(title="Shop Your Look", is_active=True)
        for i in range(4):
            HomeLifestyleTile.objects.create(section=section, label=f"Tile {i}")

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Previous look")
        self.assertNotContains(response, "Next look")

    def test_carousel_arrows_shown_beyond_four(self):
        section = HomeLifestyleSection.objects.create(title="Shop Your Look", is_active=True)
        for i in range(5):
            HomeLifestyleTile.objects.create(section=section, label=f"Tile {i}")

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Previous look")
        self.assertContains(response, "Next look")


class HomePromoBannerTestCase(TestCase):
    def test_renders_when_active(self):
        HomePromoBanner.objects.create(heading="The Gold Look. Without The Gold Price.", is_active=True)

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "The Gold Look. Without The Gold Price.")

    def test_hidden_when_inactive(self):
        HomePromoBanner.objects.create(heading="The Gold Look.", is_active=False)

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "The Gold Look.")

    def test_only_non_blank_bullets_render(self):
        HomePromoBanner.objects.create(
            heading="The Gold Look.",
            bullet_1="Gold-inspired designs",
            bullet_2="",
            bullet_3="",
            is_active=True,
        )

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Gold-inspired designs")

    def test_only_the_first_active_banner_is_shown(self):
        HomePromoBanner.objects.create(heading="Older Promo", is_active=True, display_order=1)
        HomePromoBanner.objects.create(heading="Newer Promo", is_active=True, display_order=2)

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Older Promo")
        self.assertNotContains(response, "Newer Promo")


class HomeCategorySpotlightSectionTestCase(TestCase):
    def test_renders_when_active_with_a_tile(self):
        section = HomeCategorySpotlightSection.objects.create(title="Shop Categories", is_active=True)
        HomeCategorySpotlightTile.objects.create(section=section, label="Earrings", tagline="Discover your next pair")

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Shop Categories")
        self.assertContains(response, "Earrings")
        self.assertContains(response, "Discover your next pair")

    def test_hidden_when_inactive(self):
        section = HomeCategorySpotlightSection.objects.create(title="Shop Categories", is_active=False)
        HomeCategorySpotlightTile.objects.create(section=section, label="Earrings")

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Shop Categories")

    def test_hidden_when_active_with_no_tiles(self):
        HomeCategorySpotlightSection.objects.create(title="Shop Categories", is_active=True)

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Shop Categories")


class HomeLovedBySectionTestCase(TestCase):
    def test_renders_when_active_with_a_quote(self):
        section = HomeLovedBySection.objects.create(
            title="Loved by Our Customers",
            rating_value=Decimal("4.8"),
            rating_count_label="500+ Happy Customers",
            is_active=True,
        )
        HomeLovedByQuote.objects.create(section=section, quote_text="Beautiful jewellery!", customer_name="Priya S.")

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Loved by Our Customers")
        self.assertContains(response, "4.8/5")
        self.assertContains(response, "500+ Happy Customers")
        self.assertContains(response, "Beautiful jewellery!")
        self.assertContains(response, "Priya S.")

    def test_hidden_when_inactive(self):
        section = HomeLovedBySection.objects.create(title="Loved by Our Customers", is_active=False)
        HomeLovedByQuote.objects.create(section=section, quote_text="Great!", customer_name="Neha M.")

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Loved by Our Customers")

    def test_hidden_when_active_with_no_quotes(self):
        HomeLovedBySection.objects.create(title="Loved by Our Customers", is_active=True)

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Loved by Our Customers")


class HomeTrustStripSectionTestCase(TestCase):
    def test_renders_when_active_with_an_item(self):
        section = HomeTrustStripSection.objects.create(is_active=True)
        HomeTrustStripItem.objects.create(section=section, icon="truck", label="Fast Shipping")

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Fast Shipping")
        self.assertContains(response, get_named_icon("truck"))

    def test_hidden_when_inactive(self):
        section = HomeTrustStripSection.objects.create(is_active=False)
        HomeTrustStripItem.objects.create(section=section, icon="truck", label="Fast Shipping")

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Fast Shipping")

    def test_hidden_when_active_with_no_items(self):
        HomeTrustStripSection.objects.create(title="Trust", is_active=True)

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Trust")

    def test_unrecognized_icon_falls_back_to_the_generic_icon(self):
        section = HomeTrustStripSection.objects.create(is_active=True)
        item = HomeTrustStripItem.objects.create(section=section, label="Mystery Badge")
        item.icon = "not-a-real-icon"
        item.save()

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, get_named_icon("not-a-real-icon"))
        self.assertContains(response, get_named_icon("sparkle"))


class HomeValuePropSectionTestCase(TestCase):
    def test_renders_when_active_with_an_item(self):
        section = HomeValuePropSection.objects.create(title="Why AVR Collections", is_active=True)
        HomeValuePropItem.objects.create(
            section=section, icon="gem", title="Designed to Impress", description="Contemporary styles."
        )

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Why AVR Collections")
        self.assertContains(response, "Designed to Impress")
        self.assertContains(response, "Contemporary styles.")
        self.assertContains(response, get_named_icon("gem"))

    def test_hidden_when_inactive(self):
        section = HomeValuePropSection.objects.create(title="Why AVR Collections", is_active=False)
        HomeValuePropItem.objects.create(section=section, title="Designed to Impress", description="Styles.")

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Why AVR Collections")

    def test_hidden_when_active_with_no_items(self):
        HomeValuePropSection.objects.create(title="Why AVR Collections", is_active=True)

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Why AVR Collections")


class HomeGallerySectionTestCase(TestCase):
    def test_renders_when_active_with_an_item(self):
        section = HomeGallerySection.objects.create(
            title="Behind the AVR", instagram_url="https://instagram.com/avrcollections", is_active=True
        )
        HomeGalleryItem.objects.create(section=section, caption="Packing an order")

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Behind the AVR")
        self.assertContains(response, "Packing an order")
        self.assertContains(response, "https://instagram.com/avrcollections")

    def test_hidden_when_inactive(self):
        section = HomeGallerySection.objects.create(title="Behind the AVR", is_active=False)
        HomeGalleryItem.objects.create(section=section, caption="Packing an order")

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Behind the AVR")

    def test_hidden_when_active_with_no_items(self):
        HomeGallerySection.objects.create(title="Behind the AVR", is_active=True)

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Behind the AVR")

    def test_carousel_arrows_hidden_at_five_or_fewer(self):
        section = HomeGallerySection.objects.create(title="Behind the AVR", is_active=True)
        for i in range(5):
            HomeGalleryItem.objects.create(section=section, caption=f"Photo {i}")

        response = self.client.get(reverse("pages:home"))

        self.assertNotContains(response, "Previous photo")
        self.assertNotContains(response, "Next photo")

    def test_carousel_arrows_shown_beyond_five(self):
        section = HomeGallerySection.objects.create(title="Behind the AVR", is_active=True)
        for i in range(6):
            HomeGalleryItem.objects.create(section=section, caption=f"Photo {i}")

        response = self.client.get(reverse("pages:home"))

        self.assertContains(response, "Previous photo")
        self.assertContains(response, "Next photo")
