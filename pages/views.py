"""
Static informational views: the storefront homepage, the site-wide legal
pages (Privacy Policy, Terms & Conditions, Shipping, Return & Refund,
Cancellation), the contact form, a default robots.txt, and the PWA
manifest/service worker.
"""

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static

from catalog.icons import get_category_icon
from catalog.models import Product, Review, Subcategory
from catalog.utils import with_catalog_annotations
from common.emails import send_templated_email

from .forms import ContactForm
from .models import HomeBanner, HomePriceTier, HomeSection, HomeTestimonialSection


def _section_products(section):
    base = Product.objects.filter(is_active=True).select_related("subcategory__category__department")

    if section.source == HomeSection.Source.MANUAL:
        picks = list(section.section_products.order_by("display_order").values_list("product_id", flat=True))
        by_id = {p.pk: p for p in with_catalog_annotations(base.filter(pk__in=picks))}
        return [by_id[pk] for pk in picks if pk in by_id]

    if section.source == HomeSection.Source.DEPARTMENT and section.department_id:
        qs = base.filter(subcategory__category__department=section.department_id)
    else:
        qs = base

    return list(with_catalog_annotations(qs)[: section.limit])


def _section_reviews(section):
    base = Review.objects.select_related("product", "user")

    if section.source == HomeTestimonialSection.Source.MANUAL:
        picks = list(section.section_reviews.order_by("display_order").values_list("review_id", flat=True))
        by_id = {r.pk: r for r in base.filter(pk__in=picks)}
        reviews = [by_id[pk] for pk in picks if pk in by_id]
    else:
        # LATEST: a star rating alone isn't a quotable testimonial, so only
        # reviews with actual written feedback are eligible here.
        qs = (
            base.filter(rating__gte=section.minimum_rating, is_verified_purchase=True)
            .exclude(comment="")
            .order_by("-created_at")
        )
        reviews = list(qs[: section.limit])

    # Display highest-rated testimonials first; sorted() is stable, so ties
    # keep their prior order (recency for LATEST, staff order for MANUAL).
    return sorted(reviews, key=lambda review: review.rating, reverse=True)


def home(request):
    banners = HomeBanner.objects.filter(is_active=True)
    sections = [
        {"section": section, "products": _section_products(section)}
        for section in HomeSection.objects.filter(is_active=True).select_related("department")
    ]
    testimonial_sections = [
        {"section": section, "reviews": _section_reviews(section)}
        for section in HomeTestimonialSection.objects.filter(is_active=True)
    ]
    shop_by_categories = [
        {"subcategory": subcategory, "icon_svg": get_category_icon(subcategory.name)}
        for subcategory in Subcategory.objects.select_related("category__department")
    ]
    price_tiers = HomePriceTier.objects.filter(is_active=True)
    return render(
        request,
        "pages/home.html",
        {
            "banners": banners,
            "sections": sections,
            "testimonial_sections": testimonial_sections,
            "shop_by_categories": shop_by_categories,
            "price_tiers": price_tiers,
        },
    )


def privacy_policy(request):
    return render(request, "pages/privacy_policy.html")


def terms_conditions(request):
    return render(request, "pages/terms_conditions.html")


def shipping_policy(request):
    return render(request, "pages/shipping_policy.html")


def return_refund_policy(request):
    return render(
        request,
        "pages/return_refund_policy.html",
        {"return_window_days": settings.RETURN_WINDOW_DAYS},
    )


def cancellation_policy(request):
    return render(request, "pages/cancellation_policy.html")


def contact(request):
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            contact_message = form.save()
            send_templated_email(
                "contact_message",
                {"contact_message": contact_message},
                to=settings.STAFF_NOTIFICATION_EMAIL,
            )
            messages.success(
                request,
                "Thanks for reaching out — our team will get back to you shortly.",
            )
            return redirect("pages:contact")
    else:
        initial = {}
        if request.user.is_authenticated:
            initial = {"full_name": request.user.full_name, "email": request.user.email}
        form = ContactForm(initial=initial)

    return render(request, "pages/contact.html", {"form": form})


def robots_txt(request):
    # One line per non-public URL prefix from config/urls.py — every app
    # that's entirely login/staff-gated (nothing here is meant to be
    # crawled or indexed), not just the ones under /accounts/. accounts.urls
    # itself only nests /profile/ and /store-dashboard/ under the site
    # root rather than /accounts/, so those need their own lines too.
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /accounts/",
        "Disallow: /profile/",
        "Disallow: /store-dashboard/",
        "Disallow: /cart/",
        "Disallow: /payments/",
        "Disallow: /fulfillment/",
        "Disallow: /returns/",
        "Disallow: /picweight/",
        "Allow: /",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def manifest_json(request):
    """
    PWA install metadata — name/icons/colors for "Add to Home Screen".
    Sourced from settings like every other piece of site branding (see
    SITE_NAME's comment in config/settings/base.py), so re-skinning the
    storefront via .env re-skins the installed app too, with no template
    edits needed.
    """
    manifest = {
        "name": settings.SITE_NAME,
        "short_name": settings.SITE_NAME,
        "description": settings.SITE_TAGLINE,
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        # Matches templates/base.html's bg-slate-50 body / bg-white header
        # (--color-slate-50 in static/css/tailwind_src.css) — background_color
        # is the splash-screen color while the app loads, so matching the
        # real page background avoids a flash of the wrong color.
        "background_color": "#faf8f3",
        "theme_color": "#ffffff",
        "icons": [
            {"src": static("icons/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {
                "src": static("icons/icon-maskable-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }
    return JsonResponse(manifest, content_type="application/manifest+json")


def service_worker(request):
    """
    Served at the site root (see pages/urls.py) rather than under
    /static/js/ — a service worker's default scope is limited to its own
    URL's directory, and this one needs to control the whole site to be
    installable.
    """
    return render(request, "sw.js", content_type="application/javascript")
