"""Shared Product queryset annotations and pagination used across apps that render product grids."""

from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q

PRODUCTS_PER_PAGE = 12


def with_catalog_annotations(queryset):
    """
    Annotates a Product queryset with `avg_rating`, `review_count`, and
    `variant_count` (active variants only), and restores the `-created_at`
    ordering that annotate() drops (a Django ORM quirk with aggregates over
    a reverse relation).
    """
    return queryset.annotate(
        avg_rating=Avg("reviews__rating"),
        review_count=Count("reviews", distinct=True),
        variant_count=Count("variants", distinct=True, filter=Q(variants__is_active=True)),
    ).order_by("-created_at")


def paginate_products(request, queryset, per_page=PRODUCTS_PER_PAGE):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page"))
