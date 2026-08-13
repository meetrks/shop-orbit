from django.contrib import admin
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html

from .couriers.delhivery import DelhiveryAPIError
from .models import Invoice, PackingSlip, PincodeServiceability, Shipment, ShipmentStatusHistory
from .services import assign_delhivery_waybill, sync_shipment_tracking


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = [
        "invoice_number",
        "order",
        "taxable_amount",
        "total_tax",
        "total_amount",
        "buyer_state",
        "issued_at",
    ]
    search_fields = ["invoice_number", "order__order_number", "order__user__email"]
    list_filter = ["buyer_state", "issued_at"]
    autocomplete_fields = ["order"]
    readonly_fields = [
        "order",
        "invoice_number",
        "taxable_amount",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "total_tax",
        "total_amount",
        "buyer_state",
        "seller_state",
        "issued_at",
        "created_at",
        "updated_at",
        "pdf_link",
    ]
    fields = [
        "order",
        "invoice_number",
        "taxable_amount",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "total_tax",
        "total_amount",
        "buyer_state",
        "seller_state",
        "issued_at",
        "pdf_link",
    ]

    @admin.display(description="PDF")
    def pdf_link(self, obj):
        if not obj.pdf:
            return "—"
        return format_html('<a href="{}" target="_blank">Download</a>', obj.pdf.url)

    def has_add_permission(self, request):
        # Invoices are only ever created by payments.pipeline on payment capture.
        return False


@admin.register(PackingSlip)
class PackingSlipAdmin(admin.ModelAdmin):
    list_display = ["order", "generated_at"]
    search_fields = ["order__order_number"]
    autocomplete_fields = ["order"]
    readonly_fields = ["order", "generated_at", "created_at", "updated_at", "pdf_link"]
    fields = ["order", "generated_at", "pdf_link"]

    @admin.display(description="PDF")
    def pdf_link(self, obj):
        if not obj.pdf:
            return "—"
        return format_html('<a href="{}" target="_blank">Download</a>', obj.pdf.url)

    def has_add_permission(self, request):
        return False


class ShipmentStatusHistoryInline(admin.TabularInline):
    model = ShipmentStatusHistory
    extra = 0
    fields = ["previous_status", "new_status", "changed_by", "reason", "created_at"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ["order", "carrier", "tracking_number", "status", "shipped_at", "delivered_at"]
    list_filter = ["status", "carrier"]
    search_fields = ["order__order_number", "tracking_number"]
    autocomplete_fields = ["order"]
    readonly_fields = ["order", "created_at", "updated_at"]
    fields = [
        "order",
        "carrier",
        "tracking_number",
        "tracking_url",
        "status",
        "shipped_at",
        "delivered_at",
    ]
    inlines = [ShipmentStatusHistoryInline]
    actions = ["mark_picked_up", "mark_delivered", "assign_delhivery_waybill_action", "sync_delhivery_tracking_action"]

    def has_add_permission(self, request):
        # Shipments are only ever created by payments.pipeline on payment capture.
        return False

    def save_model(self, request, obj, form, change):
        # Attributes a direct change-form edit to the staff member making
        # it — see fulfillment.signals._record_status_history.
        obj._status_change_actor = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Mark selected shipments as picked up")
    def mark_picked_up(self, request, queryset):
        updated = 0
        for shipment in queryset:
            shipment.status = Shipment.Status.PICKED_UP
            if not shipment.shipped_at:
                shipment.shipped_at = timezone.now()
            shipment._status_change_actor = request.user
            shipment.save()
            updated += 1
        self.message_user(request, f"{updated} shipment(s) marked as picked up.")

    @admin.action(description="Mark selected shipments as delivered")
    def mark_delivered(self, request, queryset):
        updated = 0
        for shipment in queryset:
            shipment.status = Shipment.Status.DELIVERED
            shipment.delivered_at = timezone.now()
            shipment._status_change_actor = request.user
            shipment.save()
            updated += 1
        self.message_user(request, f"{updated} shipment(s) marked as delivered.")

    @admin.action(description="Assign Delhivery waybill (AWB)")
    def assign_delhivery_waybill_action(self, request, queryset):
        assigned, failed = 0, 0
        for shipment in queryset:
            try:
                assign_delhivery_waybill(shipment, actor=request.user)
                assigned += 1
            except DelhiveryAPIError as exc:
                failed += 1
                self.message_user(request, f"{shipment.order.order_number}: {exc}", level="ERROR")
        if assigned:
            self.message_user(request, f"Assigned a Delhivery waybill to {assigned} shipment(s).")
        if failed:
            self.message_user(request, f"{failed} shipment(s) failed — see above.", level="ERROR")

    @admin.action(description="Sync tracking status from Delhivery")
    def sync_delhivery_tracking_action(self, request, queryset):
        updated, failed = 0, 0
        for shipment in queryset:
            try:
                if sync_shipment_tracking(shipment, actor=request.user):
                    updated += 1
            except DelhiveryAPIError as exc:
                failed += 1
                self.message_user(request, f"{shipment.order.order_number}: {exc}", level="ERROR")
        self.message_user(request, f"{updated} shipment(s) had a status change.")
        if failed:
            self.message_user(request, f"{failed} shipment(s) failed — see above.", level="ERROR")


@admin.register(PincodeServiceability)
class PincodeServiceabilityAdmin(admin.ModelAdmin):
    list_display = ["postal_code", "is_serviceable", "note"]
    list_filter = ["is_serviceable"]
    search_fields = ["postal_code"]
    fields = ["postal_code", "is_serviceable", "note"]
    change_list_template = "admin/fulfillment/pincodeserviceability_change_list.html"

    def get_urls(self):
        urls = [
            path(
                "bulk-block/",
                self.admin_site.admin_view(self.bulk_block_view),
                name="fulfillment_pincodeserviceability_bulk_block",
            ),
        ]
        return urls + super().get_urls()

    def bulk_block_view(self, request):
        if not request.user.has_perm("fulfillment.add_pincodeserviceability"):
            return HttpResponseForbidden()

        if request.method == "POST":
            raw = request.POST.get("postal_codes", "")
            note = request.POST.get("note", "").strip()
            codes = {code.strip() for code in raw.replace(",", "\n").splitlines() if code.strip()}
            created = 0
            for code in codes:
                _obj, was_created = PincodeServiceability.objects.update_or_create(
                    postal_code=code, defaults={"is_serviceable": False, "note": note}
                )
                if was_created:
                    created += 1
            self.message_user(request, f"Marked {len(codes)} pincode(s) as unserviceable ({created} new).")
            return redirect("admin:fulfillment_pincodeserviceability_changelist")

        context = {**self.admin_site.each_context(request), "title": "Bulk-block pincodes", "opts": self.model._meta}
        return render(request, "admin/fulfillment/pincode_bulk_block.html", context)
