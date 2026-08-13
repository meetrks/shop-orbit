from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import PurchaseOrder, PurchaseOrderLine, StockMovement, Supplier
from .services import create_purchase_order_from_low_stock, mark_purchase_order_ordered, receive_purchase_order_line


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    """Read-only — every row here is written by inventory.services, never edited directly."""

    list_display = [
        "created_at",
        "movement_type",
        "product",
        "variant",
        "stock_delta",
        "reserved_delta",
        "order",
        "purchase_order",
        "created_by",
    ]
    list_filter = ["movement_type", "created_at"]
    search_fields = [
        "product__title",
        "product__sku",
        "variant__sku",
        "order__order_number",
        "purchase_order__po_number",
        "note",
    ]
    autocomplete_fields = ["product", "variant", "order", "purchase_order", "created_by"]
    readonly_fields = [
        "product",
        "variant",
        "movement_type",
        "stock_delta",
        "reserved_delta",
        "order",
        "purchase_order",
        "note",
        "created_by",
        "created_at",
        "updated_at",
    ]
    list_per_page = 50
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "contact_person", "phone", "email", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "contact_person", "email", "phone"]
    readonly_fields = ["created_at", "updated_at"]
    actions = ["create_po_from_low_stock_action"]

    @admin.action(description="Create purchase order from this supplier's low-stock products")
    def create_po_from_low_stock_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one supplier.", level=messages.ERROR)
            return None
        supplier = queryset.first()
        try:
            po = create_purchase_order_from_low_stock(supplier, created_by=request.user)
        except ValueError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return None
        self.message_user(request, f"Draft purchase order {po.po_number} created.")
        return redirect(reverse("admin:inventory_purchaseorder_change", args=[po.pk]))


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 1
    fields = ["product", "variant", "quantity_ordered", "quantity_received", "unit_cost", "line_total"]
    readonly_fields = ["quantity_received", "line_total"]
    autocomplete_fields = ["product", "variant"]

    @admin.display(description="Line total")
    def line_total(self, obj):
        return obj.line_total if obj.pk else "—"


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    """
    `status` advances automatically as lines are received (see
    `inventory.services.receive_purchase_order_line`) or via the "Mark as
    ordered" action — not directly editable, same reasoning as
    `cart.Order.status`.
    """

    list_display = ["po_number", "supplier", "status", "total_cost_display", "expected_delivery_date", "created_at"]
    list_filter = ["status", "supplier"]
    search_fields = ["po_number", "supplier__name"]
    autocomplete_fields = ["supplier", "created_by"]
    readonly_fields = ["po_number", "status", "total_cost_display", "created_at", "updated_at", "receive_link"]
    inlines = [PurchaseOrderLineInline]
    list_per_page = 50
    date_hierarchy = "created_at"
    actions = ["mark_ordered_action"]

    fieldsets = (
        (None, {"fields": ("po_number", "supplier", "status", "expected_delivery_date", "notes", "created_by")}),
        ("Totals", {"fields": ("total_cost_display",)}),
        ("Receiving", {"fields": ("receive_link",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Total cost")
    def total_cost_display(self, obj):
        return f"Rs. {obj.total_cost:.2f}" if obj.pk else "—"

    @admin.display(description="Receive shipment")
    def receive_link(self, obj):
        if not obj.pk or obj.status not in (PurchaseOrder.Status.ORDERED, PurchaseOrder.Status.PARTIALLY_RECEIVED):
            return "Mark this order as “Ordered” first."
        url = reverse("admin:inventory_purchaseorder_receive", args=[obj.pk])
        return format_html('<a class="button" href="{}">Receive shipment</a>', url)

    @admin.action(description="Mark selected purchase orders as ordered")
    def mark_ordered_action(self, request, queryset):
        updated, failed = 0, 0
        for po in queryset:
            try:
                mark_purchase_order_ordered(po)
                updated += 1
            except ValueError as exc:
                failed += 1
                self.message_user(request, f"{po.po_number}: {exc}", level=messages.ERROR)
        if updated:
            self.message_user(request, f"Marked {updated} purchase order(s) as ordered.")

    def get_urls(self):
        urls = [
            path(
                "<int:po_id>/receive/",
                self.admin_site.admin_view(self.receive_view),
                name="inventory_purchaseorder_receive",
            ),
        ]
        return urls + super().get_urls()

    def receive_view(self, request, po_id):
        po = get_object_or_404(PurchaseOrder, pk=po_id)
        lines = list(po.lines.select_related("product", "variant"))

        if request.method == "POST":
            received_any = False
            for line in lines:
                raw_quantity = request.POST.get(f"line_{line.pk}", "").strip()
                if not raw_quantity:
                    continue
                try:
                    quantity = int(raw_quantity)
                except ValueError:
                    continue
                if quantity <= 0:
                    continue
                try:
                    receive_purchase_order_line(line, quantity, received_by=request.user)
                    received_any = True
                except ValueError as exc:
                    self.message_user(request, f"{line}: {exc}", level=messages.ERROR)
            if received_any:
                self.message_user(request, f"Recorded receipt against {po.po_number}.")
            return redirect(reverse("admin:inventory_purchaseorder_change", args=[po.pk]))

        context = {
            **self.admin_site.each_context(request),
            "title": f"Receive shipment for {po.po_number}",
            "po": po,
            "lines": lines,
            "opts": self.model._meta,
        }
        return render(request, "admin/inventory/receive_purchase_order.html", context)


@admin.register(PurchaseOrderLine)
class PurchaseOrderLineAdmin(admin.ModelAdmin):
    """Registered so PurchaseOrderLineInline's autocomplete lookups work; edited via the PurchaseOrder inline."""

    list_display = ["purchase_order", "product", "variant", "quantity_ordered", "quantity_received", "unit_cost"]
    search_fields = ["purchase_order__po_number", "product__title", "product__sku"]
    autocomplete_fields = ["purchase_order", "product", "variant"]
    readonly_fields = ["created_at", "updated_at"]
