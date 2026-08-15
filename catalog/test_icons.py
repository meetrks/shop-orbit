"""Tests for catalog.icons.get_category_icon's keyword matching."""

from django.test import SimpleTestCase

from catalog.icons import _RING, _SPARKLE_FALLBACK, get_category_icon


class GetCategoryIconTestCase(SimpleTestCase):
    def test_matches_by_keyword_case_insensitively(self):
        self.assertEqual(get_category_icon("Gold Rings"), get_category_icon("gold rings"))
        self.assertEqual(get_category_icon("RINGS"), _RING)

    def test_matches_real_store_categories(self):
        # These are the actual subcategory names this store uses — see
        # the seed data referenced in docs/. Each should resolve to a
        # specific icon, not the generic fallback.
        for name in [
            "Bangles & Bracelets",
            "Earrings & Studs",
            "Gold Rings",
            "Hair Clips & Hair Pins",
            "Necklaces & Chains",
        ]:
            self.assertNotEqual(get_category_icon(name), _SPARKLE_FALLBACK, name)

    def test_falls_back_for_an_unrecognized_name(self):
        self.assertEqual(get_category_icon("Something Nobody Sells"), _SPARKLE_FALLBACK)

    def test_always_returns_a_single_svg_element(self):
        for name in ["Rings", "Necklaces", "Earrings", "Bangles", "Hair Clips", "Unmatched"]:
            icon = get_category_icon(name)
            self.assertTrue(icon.strip().startswith("<svg"))
            self.assertTrue(icon.strip().endswith("</svg>"))
