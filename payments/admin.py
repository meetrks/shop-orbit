import csv
import json

from django.contrib import admin, messages
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from returns.models import ReturnRequest

from .forms import RefundForm
from .models import Payment, PaymentEvent, Refund, WebhookEvent
from .services import PaymentService


class PaymentEventInline(admin.TabularInline):
    """Read-only audit trail — every state transition this payment has gone through, and why."""

    model = PaymentEvent
    extra = 0
    can_delete = False
    fields = ["created_at", "event_type", "previous_status", "new_status"]
    readonly_fields = fields
    ordering = ["created_at"]

    def has_add_permission(self, request, obj=None):
        return False


class RefundInline(admin.TabularInline):
    model = Refund
    extra = 0
    can_delete = False
    fields = ["gateway_refund_id", "amount", "status", "reason", "initiated_by", "processed_at"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        # Refunds are only ever created through "Initiate refund" (which
        # calls the gateway), never as a bare DB row.
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "order",
        "user",
        "gateway",
        "gateway_payment_id",
        "amount",
        "method",
        "status",
        "refund_status",
        "created_at",
    ]
    list_filter = ["status", "gateway", "method", "refund_status"]
    search_fields = [
        "order__order_number",
        "user__email",
        "gateway_order_id",
        "gateway_payment_id",
        "transaction_reference",
    ]
    autocomplete_fields = ["order", "user"]
    readonly_fields = [
        "order",
        "user",
        "gateway",
        "gateway_order_id",
        "gateway_payment_id",
        "signature",
        "amount",
        "currency",
        "method",
        "status",
        "transaction_reference",
        "failure_reason",
        "refunded_amount",
        "refund_status",
        "captured_at",
        "created_at",
        "updated_at",
        "raw_response_display",
        "refund_action",
    ]
    fieldsets = (
        (None, {"fields": ("order", "user", "gateway", "status", "method")}),
        (
            "Gateway identifiers",
            {"fields": ("gateway_order_id", "gateway_payment_id", "signature", "transaction_reference")},
        ),
        ("Amount", {"fields": ("amount", "currency", "captured_at")}),
        ("Failure", {"fields": ("failure_reason",)}),
        ("Refunds", {"fields": ("refunded_amount", "refund_status", "refund_action")}),
        ("Raw gateway response", {"fields": ("raw_response_display",), "classes": ("collapse",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    inlines = [PaymentEventInline, RefundInline]
    list_per_page = 50
    date_hierarchy = "created_at"
    actions = ["reconcile_with_gateway", "export_as_csv"]

    def has_add_permission(self, request):
        # Payments are only ever created by checkout (via PaymentService), never by hand.
        return False

    @admin.display(description="Raw gateway response")
    def raw_response_display(self, obj):
        return format_html("<pre>{}</pre>", json.dumps(obj.raw_response, indent=2, default=str))

    @admin.display(description="Refund")
    def refund_action(self, obj):
        if not obj.pk or not obj.is_refundable:
            return "Not refundable"
        url = reverse("admin:payments_payment_refund", args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">Initiate refund (up to Rs. {})</a>', url, obj.remaining_refundable
        )

    @admin.action(description="Reconcile selected payments with Razorpay")
    def reconcile_with_gateway(self, request, queryset):
        service = PaymentService()
        succeeded, failed = 0, 0
        for payment in queryset:
            try:
                service.reconcile_payment(payment)
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 — surfaced to the admin user, not swallowed
                failed += 1
                self.message_user(request, f"Payment {payment.pk}: {exc}", level=messages.ERROR)
        self.message_user(request, f"Reconciled {succeeded} payment(s); {failed} failed.")

    @admin.action(description="Export selected payments as CSV")
    def export_as_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="payments.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "Payment ID",
                "Order Number",
                "Customer Email",
                "Gateway",
                "Gateway Payment ID",
                "Amount",
                "Currency",
                "Method",
                "Status",
                "Refunded Amount",
                "Refund Status",
                "Captured At",
                "Created At",
            ]
        )
        for payment in queryset.select_related("order", "user"):
            writer.writerow(
                [
                    payment.pk,
                    payment.order.order_number,
                    payment.user.email,
                    payment.gateway,
                    payment.gateway_payment_id,
                    payment.amount,
                    payment.currency,
                    payment.method,
                    payment.status,
                    payment.refunded_amount,
                    payment.refund_status,
                    payment.captured_at,
                    payment.created_at,
                ]
            )
        return response

    def get_urls(self):
        urls = [
            path(
                "<int:payment_id>/refund/",
                self.admin_site.admin_view(self.refund_view),
                name="payments_payment_refund",
            ),
        ]
        return urls + super().get_urls()

    def refund_view(self, request, payment_id):
        if not request.user.has_perm("payments.change_payment"):
            return HttpResponseForbidden()

        payment = get_object_or_404(Payment, pk=payment_id)
        remaining = payment.remaining_refundable

        return_request = None
        return_request_id = request.GET.get("return_request_id")
        if return_request_id:
            return_request = get_object_or_404(ReturnRequest, pk=return_request_id)

        success_url = (
            reverse("admin:returns_returnrequest_change", args=[return_request.pk])
            if return_request
            else reverse("admin:payments_payment_change", args=[payment.pk])
        )

        if request.method == "POST":
            form = RefundForm(request.POST)
            if form.is_valid():
                amount = form.cleaned_data["amount"]
                reason = form.cleaned_data["reason"]
                if amount > remaining:
                    form.add_error("amount", f"Cannot refund more than the remaining Rs. {remaining}.")
                else:
                    try:
                        PaymentService().initiate_refund(
                            payment,
                            amount,
                            reason=reason,
                            initiated_by=request.user,
                            return_request=return_request,
                        )
                    except Exception as exc:  # noqa: BLE001 — surfaced to the admin user
                        self.message_user(request, f"Refund failed: {exc}", level=messages.ERROR)
                    else:
                        self.message_user(request, f"Refund of Rs. {amount} initiated.")
                    return redirect(success_url)
        else:
            initial = {"amount": remaining}
            if return_request:
                initial["amount"] = min(return_request.suggested_refund_amount, remaining)
                initial["reason"] = f"Return {return_request.pk}: {return_request.get_reason_display()}"
            form = RefundForm(initial=initial)

        context = {
            **self.admin_site.each_context(request),
            "title": f"Initiate refund for payment {payment.pk}",
            "payment": payment,
            "remaining": remaining,
            "return_request": return_request,
            "form": form,
            "opts": self.model._meta,
        }
        return render(request, "admin/payments/refund_form.html", context)


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ["id", "payment", "amount", "status", "initiated_by", "processed_at", "created_at"]
    list_filter = ["status"]
    search_fields = ["payment__order__order_number", "gateway_refund_id", "payment__gateway_payment_id"]
    autocomplete_fields = ["payment", "initiated_by"]
    readonly_fields = [
        "payment",
        "gateway_refund_id",
        "amount",
        "status",
        "reason",
        "notes",
        "initiated_by",
        "failure_reason",
        "processed_at",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ["id", "gateway", "event_type", "dedupe_key", "processed_at", "created_at"]
    list_filter = ["gateway", "event_type"]
    search_fields = ["dedupe_key", "event_type"]
    readonly_fields = [
        "gateway",
        "dedupe_key",
        "event_type",
        "payload_display",
        "processed_at",
        "processing_error",
        "created_at",
        "updated_at",
    ]
    exclude = ["payload"]

    @admin.display(description="Payload")
    def payload_display(self, obj):
        return format_html("<pre>{}</pre>", json.dumps(obj.payload, indent=2, default=str))

    def has_add_permission(self, request):
        return False
