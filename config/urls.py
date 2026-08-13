"""
Root URL configuration for the shoporbit project.

Each app owns its own `urls.py` and is included here under a namespace so
that templates can reverse URLs with `{% url 'app_name:view_name' %}`.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from catalog.admin import store_stats
from catalog.sitemaps import CategorySitemap, DepartmentSitemap, ProductSitemap, SubcategorySitemap
from common.health import liveness_view, readiness_view
from pages.sitemaps import StaticViewSitemap

sitemaps = {
    "static": StaticViewSitemap,
    "products": ProductSitemap,
    "departments": DepartmentSitemap,
    "categories": CategorySitemap,
    "subcategories": SubcategorySitemap,
}

admin.site.site_header = settings.DJANGO_ADMIN_SITE_HEADER
admin.site.site_title = settings.DJANGO_ADMIN_SITE_TITLE
admin.site.index_title = "Storefront Administration"

# Injects store_stats() into the admin dashboard's context — see
# templates/admin/index.html. AdminSite.index() already accepts an
# extra_context dict; this wraps the bound method once at URLconf-import
# time (the same timing config/urls.py already relies on for the
# site_header/site_title lines above) rather than requiring every app's
# admin.py to register against a custom AdminSite subclass.
_default_admin_index = admin.site.index


def _admin_index_with_store_stats(request, extra_context=None):
    context = {**(extra_context or {}), **store_stats()}
    return _default_admin_index(request, context)


admin.site.index = _admin_index_with_store_stats

urlpatterns = [
    path("health/live/", liveness_view, name="health_live"),
    path("health/ready/", readiness_view, name="health_ready"),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("cart/", include("cart.urls")),
    path("payments/", include("payments.urls")),
    path("fulfillment/", include("fulfillment.urls")),
    path("returns/", include("returns.urls")),
    path("picweight/", include("picweight.urls")),
    path("", include("catalog.urls")),
    path("", include("pages.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
