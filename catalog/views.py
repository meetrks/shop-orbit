"""Public storefront views for browsing the taxonomy and product catalog."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db.models import F
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .barcodes import generate_barcode_png
from .forms import ReviewForm
from .models import Category, Department, Product, ProductVariant, Review, StockAlert, Subcategory
from .search import search_products
from .utils import paginate_products, with_catalog_annotations

# "relevance" maps to None rather than an order_by string: when it's
# active, the ordering search_products() already applied (-rank,
# -created_at, pk) is left alone instead of being overridden below.
SORT_CHOICES = {
    "newest": "-created_at",
    "price_asc": "effective_price",
    "price_desc": "-effective_price",
    "rating": "-avg_rating",
    "relevance": None,
}


def _has_purchased(user, product):
    from cart.models import Order

    return Order.objects.filter(
        user=user,
        items__product=product,
        status__in=Order.PAID_STATUSES,
    ).exists()


def _parse_price(raw):
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _apply_sort_and_filters(request, products, has_query=False):
    """
    Applies the price-range, in-stock, and sort GET params shared by every
    catalog listing view, and returns both the resulting queryset and the
    parsed filter state (for repopulating the form / building pagination
    links in the template).

    `has_query` is whether a search term is active: with no explicit
    `sort` GET param, results default to relevance-ranked (whatever order
    search_products() already applied) instead of newest-first, though an
    explicit `sort` still takes precedence either way.
    """
    min_price = request.GET.get("min_price", "").strip()
    max_price = request.GET.get("max_price", "").strip()
    in_stock_only = request.GET.get("in_stock") == "1"
    requested_sort = request.GET.get("sort")
    if requested_sort in SORT_CHOICES:
        sort = requested_sort
    else:
        sort = "relevance" if has_query else "newest"

    products = products.annotate(
        effective_price=Coalesce("discount_price", "price") + F("delivery_charge") + F("other_fee")
    )

    min_price_value = _parse_price(min_price) if min_price else None
    if min_price_value is not None:
        products = products.filter(effective_price__gte=min_price_value)

    max_price_value = _parse_price(max_price) if max_price else None
    if max_price_value is not None:
        products = products.filter(effective_price__lte=max_price_value)

    if in_stock_only:
        # available-to-sell, not raw stock_count: a unit reserved by
        # someone else's in-flight checkout shouldn't show up here either.
        products = products.filter(stock_count__gt=F("reserved_count"))

    order_by_field = SORT_CHOICES[sort]
    if order_by_field is not None:
        products = products.order_by(order_by_field)

    filter_state = {
        "sort": sort,
        "min_price": min_price,
        "max_price": max_price,
        "in_stock_only": in_stock_only,
    }
    return products, filter_state


def _preserved_querystring(request):
    """The current GET querystring minus `page`, for pagination links that keep filters applied."""
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


def _fallback_products(limit=8):
    """A modest 'you might like' row shown under an empty search result — newest, in-stock products site-wide."""
    return with_catalog_annotations(Product.objects.filter(is_active=True, stock_count__gt=F("reserved_count")))[
        :limit
    ]


def product_list(request):
    """The main catalog grid, optionally filtered by a search query."""
    products = with_catalog_annotations(
        Product.objects.filter(is_active=True).select_related("subcategory__category__department")
    )
    query = request.GET.get("q", "").strip()
    if query:
        products = search_products(products, query)
    products, filter_state = _apply_sort_and_filters(request, products, has_query=bool(query))
    page_obj = paginate_products(request, products)

    context = {
        "page_obj": page_obj,
        "search_query": query,
        "heading": "All Products",
        "preserved_querystring": _preserved_querystring(request),
        **filter_state,
    }
    if query and not page_obj.object_list:
        context["fallback_products"] = _fallback_products()
    return render(request, "catalog/product_list.html", context)


def department_detail(request, department_slug):
    department = get_object_or_404(Department, slug=department_slug)
    products = with_catalog_annotations(
        Product.objects.filter(is_active=True, subcategory__category__department=department).select_related(
            "subcategory__category__department"
        )
    )
    query = request.GET.get("q", "").strip()
    if query:
        products = search_products(products, query)
    products, filter_state = _apply_sort_and_filters(request, products, has_query=bool(query))
    page_obj = paginate_products(request, products)

    context = {
        "page_obj": page_obj,
        "search_query": query,
        "heading": department.name,
        "department": department,
        "preserved_querystring": _preserved_querystring(request),
        **filter_state,
    }
    if query and not page_obj.object_list:
        context["fallback_products"] = _fallback_products()
    return render(request, "catalog/product_list.html", context)


def category_detail(request, department_slug, category_slug):
    department = get_object_or_404(Department, slug=department_slug)
    category = get_object_or_404(Category, department=department, slug=category_slug)
    products = with_catalog_annotations(
        Product.objects.filter(is_active=True, subcategory__category=category).select_related(
            "subcategory__category__department"
        )
    )
    query = request.GET.get("q", "").strip()
    if query:
        products = search_products(products, query)
    products, filter_state = _apply_sort_and_filters(request, products, has_query=bool(query))
    page_obj = paginate_products(request, products)

    context = {
        "page_obj": page_obj,
        "search_query": query,
        "heading": f"{department.name} / {category.name}",
        "department": department,
        "category": category,
        "preserved_querystring": _preserved_querystring(request),
        **filter_state,
    }
    if query and not page_obj.object_list:
        context["fallback_products"] = _fallback_products()
    return render(request, "catalog/product_list.html", context)


def subcategory_detail(request, department_slug, category_slug, subcategory_slug):
    department = get_object_or_404(Department, slug=department_slug)
    category = get_object_or_404(Category, department=department, slug=category_slug)
    subcategory = get_object_or_404(Subcategory, category=category, slug=subcategory_slug)
    products = with_catalog_annotations(
        Product.objects.filter(is_active=True, subcategory=subcategory).select_related(
            "subcategory__category__department"
        )
    )
    query = request.GET.get("q", "").strip()
    if query:
        products = search_products(products, query)
    products, filter_state = _apply_sort_and_filters(request, products, has_query=bool(query))
    page_obj = paginate_products(request, products)

    context = {
        "page_obj": page_obj,
        "search_query": query,
        "heading": f"{department.name} / {category.name} / {subcategory.name}",
        "department": department,
        "category": category,
        "subcategory": subcategory,
        "preserved_querystring": _preserved_querystring(request),
        **filter_state,
    }
    if query and not page_obj.object_list:
        context["fallback_products"] = _fallback_products()
    return render(request, "catalog/product_list.html", context)


def product_detail(request, product_slug):
    product = get_object_or_404(
        with_catalog_annotations(Product.objects.select_related("subcategory__category__department")),
        slug=product_slug,
        is_active=True,
    )
    related_products = with_catalog_annotations(
        Product.objects.filter(subcategory=product.subcategory, is_active=True)
        .exclude(pk=product.pk)
        .select_related("subcategory")
    )[:4]

    reviews = product.reviews.select_related("user")
    user_review = None
    review_form = None
    has_purchased = False
    if request.user.is_authenticated:
        user_review = reviews.filter(user=request.user).first()
        has_purchased = _has_purchased(request.user, product)
        if user_review is None and has_purchased:
            review_form = ReviewForm()

    from picweight.models import SupplierImageJob

    zoom_job = (
        product.picweight_jobs.filter(status=SupplierImageJob.Status.PROCESSED).exclude(ultra_zoom_image="").first()
    )

    context = {
        "product": product,
        "related_products": related_products,
        "reviews": reviews,
        "user_review": user_review,
        "review_form": review_form,
        "has_purchased": has_purchased,
        "variants": product.variants.filter(is_active=True),
        "zoom_image_url": zoom_job.ultra_zoom_image.url if zoom_job else None,
        "whatsapp_message": f"Hi! I'm interested in {product.title} ({product.sku}).",
    }
    return render(request, "catalog/product_detail.html", context)


@login_required
@require_POST
def submit_review(request, product_slug):
    product = get_object_or_404(Product, slug=product_slug, is_active=True)

    if Review.objects.filter(product=product, user=request.user).exists():
        messages.warning(request, "You've already reviewed this product.")
        return redirect(product.get_absolute_url())

    if not _has_purchased(request.user, product):
        messages.error(request, "Only customers who've purchased this product can review it.")
        return redirect(product.get_absolute_url())

    form = ReviewForm(request.POST)
    if form.is_valid():
        review = form.save(commit=False)
        review.product = product
        review.user = request.user
        review.is_verified_purchase = True
        review.save()
        messages.success(request, "Thanks for your review!")
    else:
        messages.error(request, "Please choose a rating between 1 and 5 stars.")

    return redirect(product.get_absolute_url())


@login_required
def product_barcode(request, product_slug):
    """A printable Code128 barcode PNG for this product's SKU — staff only."""
    product = get_object_or_404(Product, slug=product_slug)
    if not request.user.is_staff:
        raise PermissionDenied

    return HttpResponse(generate_barcode_png(product.sku).getvalue(), content_type="image/png")


@login_required
def variant_barcode(request, product_slug, variant_id):
    """A printable Code128 barcode PNG for one variant's own SKU — staff only."""
    variant = get_object_or_404(ProductVariant, pk=variant_id, product__slug=product_slug)
    if not request.user.is_staff:
        raise PermissionDenied

    return HttpResponse(generate_barcode_png(variant.sku).getvalue(), content_type="image/png")


@require_POST
def request_stock_alert(request, product_slug):
    """
    "Notify me when back in stock" signup. Open to anonymous visitors, not
    just logged-in buyers — matching the contact form elsewhere in this app.
    """
    product = get_object_or_404(Product, slug=product_slug, is_active=True)

    variant = None
    variant_id = request.POST.get("variant_id")
    if variant_id:
        variant = get_object_or_404(ProductVariant, pk=variant_id, product=product)

    if request.user.is_authenticated:
        email = request.user.email
    else:
        email = request.POST.get("email", "").strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            messages.error(request, "Please enter a valid email address.")
            return redirect(product.get_absolute_url())

    StockAlert.objects.get_or_create(
        product=product,
        variant=variant,
        email=email,
        defaults={"user": request.user if request.user.is_authenticated else None},
    )
    messages.success(request, "We'll email you as soon as this is back in stock.")
    return redirect(product.get_absolute_url())
