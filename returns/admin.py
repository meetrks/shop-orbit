from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import ReturnRequest, ReturnRequestLine, ReturnShipment
from .services import approve_return, mark_return_received, reject_return


class ReturnRequestLineInline(admin.TabularInline):
    """Read-only — lines are created by `returns.services.request_return` and updated only via the receive view."""

    model = ReturnRequestLine
    extra = 0
    can_delete = False
    fields = ["order_item", "quantity", "restocked", "condition_note"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class ReturnShipmentInline(admin.StackedInline):
    """
    The reverse-pickup counterpart to `fulfillment.Shipment`. Only ever
    created by `returns.services.approve_return`, but editable here —
    staff may need to correct tracking details or mark a parcel LOST.
    """

    model = ReturnShipment
    max_num = 1
    can_delete = False
    fields = ["carrier", "tracking_number", "tracking_url", "status", "shipped_at", "delivered_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ReturnRequest)
class ReturnRequestAdmin(admin.ModelAdmin):
    """
    `status` advances through the workflow in `returns.services` — approve/
    reject actions here, receiving via a dedicated view, refunding by
    handing off to the existing Payments "Initiate refund" form.
    """

    list_display = ["id", "order", "user", "status", "reason", "created_at"]
    list_filter = ["status", "reason"]
    search_fields = ["order__order_number", "user__email"]
    autocomplete_fields = ["order", "user", "reviewed_by"]
    readonly_fields = [
        "order",
        "user",
        "status",
        "reason",
        "comments",
        "reviewed_by",
        "reviewed_at",
        "received_at",
        "suggested_refund_amount_display",
        "receive_link",
        "refund_action",
        "created_at",
        "updated_at",
    ]
    inlines = [ReturnRequestLineInline, ReturnShipmentInline]
    list_per_page = 50
    date_hierarchy = "created_at"
    actions = ["approve_action", "reject_action"]

    fieldsets = (
        (None, {"fields": ("order", "user", "status", "reason", "comments")}),
        ("Staff review", {"fields": ("staff_notes", "reviewed_by", "reviewed_at")}),
        ("Receiving", {"fields": ("received_at", "receive_link")}),
        ("Refund", {"fields": ("suggested_refund_amount_display", "refund_action")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def has_add_permission(self, request):
        # Return requests are only ever created by the buyer-facing flow (returns.services.request_return).
        return False

    @admin.display(description="Suggested refund amount")
    def suggested_refund_amount_display(self, obj):
        return f"Rs. {obj.suggested_refund_amount:.2f}" if obj.pk else "—"

    @admin.display(description="Receive shipment")
    def receive_link(self, obj):
        if not obj.pk or obj.status != ReturnRequest.Status.APPROVED:
            return "Approve this request first."
        url = reverse("admin:returns_returnrequest_receive", args=[obj.pk])
        return format_html('<a class="button" href="{}">Receive returned item(s)</a>', url)

    @admin.display(description="Refund")
    def refund_action(self, obj):
        if not obj.pk or obj.status != ReturnRequest.Status.RECEIVED:
            return "Not ready for refund yet."
        payment = obj.refundable_payment
        if payment is None:
            return "No refundable payment found for this order."
        url = reverse("admin:payments_payment_refund", args=[payment.pk]) + f"?return_request_id={obj.pk}"
        return format_html(
            '<a class="button" href="{}">Initiate refund (suggested Rs. {})</a>', url, obj.suggested_refund_amount
        )

    @admin.action(description="Approve selected return request")
    def approve_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one return request.", level=messages.ERROR)
            return
        return_request = queryset.first()
        try:
            approve_return(return_request, reviewed_by=request.user, staff_notes=return_request.staff_notes)
        except ValueError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return
        self.message_user(request, f"Return {return_request.pk} approved.")

    @admin.action(description="Reject selected return request")
    def reject_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one return request.", level=messages.ERROR)
            return
        return_request = queryset.first()
        if not return_request.staff_notes:
            self.message_user(
                request,
                "Add a note in Staff notes explaining the rejection before rejecting.",
                level=messages.ERROR,
            )
            return
        try:
            reject_return(return_request, reviewed_by=request.user, staff_notes=return_request.staff_notes)
        except ValueError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return
        self.message_user(request, f"Return {return_request.pk} rejected.")

    def get_urls(self):
        urls = [
            path(
                "<int:return_id>/receive/",
                self.admin_site.admin_view(self.receive_view),
                name="returns_returnrequest_receive",
            ),
        ]
        return urls + super().get_urls()

    def receive_view(self, request, return_id):
        return_request = get_object_or_404(ReturnRequest, pk=return_id)
        lines = list(return_request.lines.select_related("order_item__product", "order_item__variant"))

        if return_request.status != ReturnRequest.Status.APPROVED:
            self.message_user(
                request,
                f"Return {return_request.pk} must be Approved before it can be received.",
                level=messages.ERROR,
            )
            return redirect(reverse("admin:returns_returnrequest_change", args=[return_request.pk]))

        if request.method == "POST":
            line_decisions = {
                line.pk: {
                    "restock": bool(request.POST.get(f"restock_{line.pk}")),
                    "condition_note": request.POST.get(f"condition_note_{line.pk}", "").strip(),
                }
                for line in lines
            }
            try:
                mark_return_received(return_request, line_decisions=line_decisions, received_by=request.user)
            except ValueError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
            else:
                self.message_user(request, f"Recorded receipt for return {return_request.pk}.")
            return redirect(reverse("admin:returns_returnrequest_change", args=[return_request.pk]))

        context = {
            **self.admin_site.each_context(request),
            "title": f"Receive returned item(s) for return {return_request.pk}",
            "return_request": return_request,
            "lines": lines,
            "opts": self.model._meta,
        }
        return render(request, "admin/returns/receive_form.html", context)
