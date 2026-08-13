"""Sitemap for the site's static, non-catalog pages."""

from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.5

    def items(self):
        return [
            "pages:home",
            "pages:privacy_policy",
            "pages:terms_conditions",
            "pages:shipping_policy",
            "pages:return_refund_policy",
            "pages:cancellation_policy",
            "pages:contact",
            "catalog:product_list",
        ]

    def location(self, item):
        return reverse(item)
