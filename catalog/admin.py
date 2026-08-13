from django.contrib import admin, messages
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from eav.admin import BaseEntityAdmin
from eav.forms import BaseDynamicEntityForm

from inventory.forms import StockAdjustmentForm
from inventory.services import record_adjustment

from .models import (
    Category,
    Department,
    Product,
    ProductImage,
    ProductVariant,
    Review,
    StockAlert,
    Subcategory,
)


class CategoryInline(admin.TabularInline):
    model = Category
    extra = 1
    prepopulated_fields = {"slug": ("name",)}
    show_change_link = True


class SubcategoryInline(admin.TabularInline):
    model = Subcategory
    extra = 1
    prepopulated_fields = {"slug": ("name",)}
    show_change_link = True


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "display_order", "category_count"]
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ["name"]
    inlines = [CategoryInline]

    @admin.display(description="Categories")
    def category_count(self, obj):
        return obj.categories.count()


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "department", "slug", "display_order", "subcategory_count"]
    list_filter = ["department"]
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ["name", "department__name"]
    inlines = [SubcategoryInline]

    @admin.display(description="Subcategories")
    def subcategory_count(self, obj):
        return obj.subcategories.count()


@admin.register(Subcategory)
class SubcategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "parent_department", "slug", "display_order", "product_count"]
    list_filter = ["category__department", "category"]
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ["name", "category__name", "category__department__name"]

    @admin.display(description="Department")
    def parent_department(self, obj):
        return obj.category.department.name

    @admin.display(description="Products")
    def product_count(self, obj):
        return obj.products.count()


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 2
    fields = ["image", "alt_text", "is_primary", "display_order"]


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = [
        "sku",
        "attributes",
        "price",
        "discount_price",
        "stock_count",
        "reserved_count",
        "low_stock_threshold",
        "thumbnail",
        "is_active",
        "display_order",
    ]
    readonly_fields = ["reserved_count"]


class LowStockFilter(admin.SimpleListFilter):
    """Flags products at or below their configured low-stock threshold."""

    title = "stock alert"
    parameter_name = "stock_alert"

    def lookups(self, request, model_admin):
        return [
            ("low", "Low stock (at or below threshold)"),
            ("out", "Out of stock"),
            ("healthy", "Healthy stock"),
        ]

    def queryset(self, request, queryset):
        # Based on available-to-sell (stock minus reservations for
        # in-flight checkouts), not raw stock_count.
        if self.value() == "low":
            return queryset.filter(
                stock_count__gt=F("reserved_count"),
                stock_count__lte=F("reserved_count") + F("low_stock_threshold"),
            )
        if self.value() == "out":
            return queryset.filter(stock_count__lte=F("reserved_count"))
        if self.value() == "healthy":
            return queryset.filter(stock_count__gt=F("reserved_count") + F("low_stock_threshold"))
        return queryset


class ProductForm(BaseDynamicEntityForm):
    class Meta:
        model = Product
        fields = "__all__"


@admin.register(Product)
class ProductAdmin(BaseEntityAdmin):
    form = ProductForm
    eav_fieldset_title = "Custom properties"
    eav_fieldset_description = (
        "Extra product-specific details. Add new property types under "
        "Catalog › Attributes; any attribute added there becomes editable "
        "here and shows on the product page automatically."
    )
    list_display = [
        "title",
        "sku",
        "subcategory",
        "price",
        "discount_price",
        "stock_count",
        "reserved_count",
        "variant_count",
        "stock_badge",
        "is_active",
        "created_at",
    ]
    list_filter = [
        "is_active",
        LowStockFilter,
        "subcategory__category__department",
        "subcategory__category",
        "subcategory",
    ]
    search_fields = [
        "title",
        "sku",
        "slug",
        "variants__sku",
    ]
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ["subcategory", "default_supplier"]
    readonly_fields = ["created_at", "updated_at", "barcode_preview", "reserved_count"]
    inlines = [ProductVariantInline, ProductImageInline]
    list_per_page = 50
    actions = ["adjust_stock_action"]

    @admin.display(description="Variants")
    def variant_count(self, obj):
        return obj.variants.count()

    @admin.action(description="Adjust stock (select exactly one product)")
    def adjust_stock_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one product to adjust its stock.", level=messages.ERROR)
            return None
        product = queryset.first()
        return redirect(reverse("admin:catalog_product_adjust_stock", args=[product.pk]))

    def get_urls(self):
        urls = [
            path(
                "<int:product_id>/adjust-stock/",
                self.admin_site.admin_view(self.adjust_stock_view),
                name="catalog_product_adjust_stock",
            ),
        ]
        return urls + super().get_urls()

    def adjust_stock_view(self, request, product_id):
        product = get_object_or_404(Product, pk=product_id)

        if request.method == "POST":
            form = StockAdjustmentForm(request.POST)
            if form.is_valid():
                record_adjustment(
                    product,
                    delta=form.cleaned_data["delta"],
                    note=form.cleaned_data["note"],
                    created_by=request.user,
                )
                self.message_user(request, f"Stock adjusted for {product.title}.")
                return redirect(reverse("admin:catalog_product_change", args=[product.pk]))
        else:
            form = StockAdjustmentForm()

        context = {
            **self.admin_site.each_context(request),
            "title": f"Adjust stock for {product.title}",
            "target": product,
            "current_stock": product.stock_count,
            "form": form,
            "opts": self.model._meta,
        }
        return render(request, "admin/inventory/adjust_stock_form.html", context)

    fieldsets = (
        (None, {"fields": ("title", "slug", "sku", "description", "subcategory")}),
        ("Pricing", {"fields": ("price", "discount_price", "delivery_charge", "other_fee")}),
        (
            "Cost & profitability",
            {
                "fields": ("cost_price",),
                "description": "Not shown to buyers — feeds the profitability report on the Store Dashboard.",
            },
        ),
        ("Tax", {"fields": ("hsn_code", "gst_rate")}),
        (
            "Inventory",
            {
                "fields": ("stock_count", "reserved_count", "low_stock_threshold", "default_supplier", "is_active"),
                "description": (
                    "Reserved units are held automatically for in-flight checkouts and can't be "
                    "edited directly — see Inventory › Stock movements for the full history, or use "
                    'the "Adjust stock" action for manual corrections.'
                ),
            },
        ),
        ("Media", {"fields": ("thumbnail",)}),
        ("SEO", {"fields": ("meta_title", "meta_description", "meta_keywords")}),
        ("Barcode", {"fields": ("barcode_preview",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Barcode label")
    def barcode_preview(self, obj):
        if not obj.pk:
            return "Save the product first to generate its barcode."
        url = reverse("catalog:product_barcode", args=[obj.slug])
        return format_html('<img src="{}" alt="Barcode for {}">', url, obj.sku)

    @admin.display(description="Stock status")
    def stock_badge(self, obj):
        colors = {
            "in_stock": "#0284c7",
            "low_stock": "#d97706",
            "out_of_stock": "#dc2626",
        }
        color = colors.get(obj.stock_status, "#0284c7")
        return format_html(
            '<span style="color: white; background-color: {}; padding: 2px 8px; '
            'border-radius: 999px; font-size: 11px;">{}</span>',
            color,
            obj.stock_status.replace("_", " ").title(),
        )

    def get_search_results(self, request, queryset, search_term):
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        if search_term:
            queryset |= self.model.objects.filter(Q(sku__icontains=search_term))
        return queryset, use_distinct


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    """
    Registered mainly so `autocomplete_fields` referencing variants (e.g.
    cart/admin.py's CartItemInline) has a searchable target; day-to-day
    variant editing normally happens via the inline on ProductAdmin.
    """

    list_display = ["sku", "product", "label", "stock_count", "reserved_count", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["sku", "product__title", "product__sku"]
    autocomplete_fields = ["product"]
    readonly_fields = ["created_at", "updated_at", "reserved_count"]
    actions = ["adjust_stock_action"]

    @admin.action(description="Adjust stock (select exactly one variant)")
    def adjust_stock_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one variant to adjust its stock.", level=messages.ERROR)
            return None
        variant = queryset.first()
        return redirect(reverse("admin:catalog_productvariant_adjust_stock", args=[variant.pk]))

    def get_urls(self):
        urls = [
            path(
                "<int:variant_id>/adjust-stock/",
                self.admin_site.admin_view(self.adjust_stock_view),
                name="catalog_productvariant_adjust_stock",
            ),
        ]
        return urls + super().get_urls()

    def adjust_stock_view(self, request, variant_id):
        variant = get_object_or_404(ProductVariant, pk=variant_id)

        if request.method == "POST":
            form = StockAdjustmentForm(request.POST)
            if form.is_valid():
                record_adjustment(
                    variant.product,
                    variant=variant,
                    delta=form.cleaned_data["delta"],
                    note=form.cleaned_data["note"],
                    created_by=request.user,
                )
                self.message_user(request, f"Stock adjusted for {variant}.")
                return redirect(reverse("admin:catalog_productvariant_change", args=[variant.pk]))
        else:
            form = StockAdjustmentForm()

        context = {
            **self.admin_site.each_context(request),
            "title": f"Adjust stock for {variant}",
            "target": variant,
            "current_stock": variant.stock_count,
            "form": form,
            "opts": self.model._meta,
        }
        return render(request, "admin/inventory/adjust_stock_form.html", context)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["product", "user", "rating", "is_verified_purchase", "created_at"]
    list_filter = ["rating", "is_verified_purchase", "created_at"]
    search_fields = ["product__title", "product__sku", "user__email", "user__full_name", "comment"]
    autocomplete_fields = ["product", "user"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(StockAlert)
class StockAlertAdmin(admin.ModelAdmin):
    list_display = ["product", "variant", "email", "notified_at", "created_at"]
    list_filter = ["notified_at", "created_at"]
    search_fields = ["product__title", "product__sku", "email"]
    autocomplete_fields = ["product", "variant", "user"]
    readonly_fields = ["created_at", "updated_at", "notified_at"]


def store_stats():
    """
    Store-wide product/order stats for the admin dashboard (see
    templates/admin/index.html and config/urls.py, which wires this into
    admin.site.index's extra_context). Used to be scoped per-seller (see
    the removed accounts.views.seller_dashboard) — with a single
    operator, these numbers are simply store-wide now.
    """
    from django.db.models import F, Sum

    from cart.models import Order, OrderItem
    from picweight.models import SupplierImageJob

    products = Product.objects.all()

    order_items = OrderItem.objects.select_related("order", "product").order_by("-created_at")
    confirmed_revenue = (
        order_items.filter(order__status__in=Order.PAID_STATUSES).aggregate(
            total=Sum(F("unit_price") * F("quantity"))
        )["total"]
        or 0
    )

    return {
        "store_product_count": products.count(),
        # Based on available-to-sell (stock minus reservations for
        # in-flight checkouts), not raw stock_count.
        "store_low_stock_count": products.filter(
            stock_count__gt=F("reserved_count"),
            stock_count__lte=F("reserved_count") + F("low_stock_threshold"),
        ).count(),
        "store_out_of_stock_count": products.filter(stock_count__lte=F("reserved_count")).count(),
        "store_confirmed_revenue": confirmed_revenue,
        "store_awaiting_payment_count": Order.objects.filter(status=Order.Status.AWAITING_PAYMENT).count(),
        "store_recent_order_items": order_items[:5],
        "store_recent_picweight_jobs": SupplierImageJob.objects.order_by("-created_at")[:6],
    }
