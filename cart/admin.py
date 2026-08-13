from django.contrib import admin, messages

from .models import (
    Cart,
    CartItem,
    Coupon,
    CouponRedemption,
    Order,
    OrderItem,
    OrderStatusHistory,
    Wishlist,
    WishlistItem,
)
from .services import accept_order, cancel_order, mark_order_packed


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    autocomplete_fields = ["product", "variant"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ["user", "total_items", "subtotal", "updated_at"]
    search_fields = ["user__email", "user__full_name"]
    inlines = [CartItemInline]


class WishlistItemInline(admin.TabularInline):
    model = WishlistItem
    extra = 0
    autocomplete_fields = ["product"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ["user", "item_count", "updated_at"]
    search_fields = ["user__email", "user__full_name"]
    inlines = [WishlistItemInline]

    @admin.display(description="Items")
    def item_count(self, obj):
        return obj.items.count()


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = [
        "product",
        "product_title",
        "product_sku",
        "variant_label",
        "variant_sku",
        "unit_price",
        "quantity",
        "line_total",
    ]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        # Items are a frozen snapshot of the cart at checkout, never
        # hand-entered — same reasoning as Payment.has_add_permission.
        # Also sidesteps a TypeError: with add allowed, Django renders a
        # hidden "empty form" template row on every page load for the
        # "Add another" JS button, and line_total(None * None) blows up
        # on that unsaved, all-blank row.
        return False

    @admin.display(description="Line total")
    def line_total(self, obj):
        return obj.line_total


class OrderStatusHistoryInline(admin.TabularInline):
    """Read-only audit trail — every status transition this order has gone through, and why."""

    model = OrderStatusHistory
    extra = 0
    can_delete = False
    fields = ["created_at", "previous_status", "new_status", "changed_by", "reason"]
    readonly_fields = fields
    ordering = ["created_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """
    Real-time order history for staff. Every order lands here in
    `AWAITING_PAYMENT` and advances to `PAYMENT_CONFIRMED` automatically
    once Razorpay reports a captured payment (see `payments.pipeline`) —
    staff no longer flip that transition manually. Payment details,
    refunds, invoices, and shipment tracking live in the Payments and
    Fulfillment admin sections respectively (linked below); this page
    stays focused on the order/shipping side.

    `status` is read-only here — every transition (payment capture/
    failure, shipment updates, cancellation) goes through code that also
    handles the side effects (stock, refund-needed notices, audit trail).
    Use the "Cancel selected orders" action for cancellation rather than
    editing status directly.
    """

    list_display = [
        "order_number",
        "user",
        "status",
        "subtotal_amount",
        "discount_amount",
        "coupon_code",
        "total_quantity",
        "created_at",
    ]
    list_filter = ["status", "created_at"]
    search_fields = [
        "order_number",
        "user__email",
        "user__full_name",
        "shipping_phone_number",
        "coupon_code",
    ]
    readonly_fields = [
        "order_number",
        "status",
        "subtotal_amount",
        "discount_amount",
        "coupon_code",
        "created_at",
        "updated_at",
    ]
    inlines = [OrderItemInline, OrderStatusHistoryInline]
    list_per_page = 50
    date_hierarchy = "created_at"
    actions = [
        "cancel_selected_orders",
        "accept_order_action",
        "mark_packed_action",
    ]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "order_number",
                    "user",
                    "status",
                    "subtotal_amount",
                    "discount_amount",
                    "coupon_code",
                )
            },
        ),
        (
            "Shipping details",
            {
                "fields": (
                    "shipping_full_name",
                    "shipping_phone_number",
                    "shipping_address_line1",
                    "shipping_address_line2",
                    "shipping_city",
                    "shipping_state",
                    "shipping_postal_code",
                    "shipping_country",
                )
            },
        ),
        ("Notes", {"fields": ("customer_note",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.action(description="Cancel selected orders")
    def cancel_selected_orders(self, request, queryset):
        cancelled, failed = 0, 0
        for order in queryset:
            try:
                cancel_order(order, cancelled_by=request.user, reason="Cancelled by staff")
            except ValueError as exc:
                failed += 1
                self.message_user(request, f"Order {order.order_number}: {exc}", level=messages.ERROR)
            else:
                cancelled += 1
        if cancelled:
            self.message_user(request, f"Cancelled {cancelled} order(s).")
        if failed:
            self.message_user(request, f"{failed} order(s) could not be cancelled.", level=messages.WARNING)

    def _run_fulfillment_transition(self, request, queryset, transition_fn, verb):
        """Shared runner for the accepted/packed actions — same try/succeed/fail shape as cancellation."""
        succeeded, failed = 0, 0
        for order in queryset:
            try:
                transition_fn(order, actor=request.user)
            except ValueError as exc:
                failed += 1
                self.message_user(request, f"Order {order.order_number}: {exc}", level=messages.ERROR)
            else:
                succeeded += 1
        if succeeded:
            self.message_user(request, f"{succeeded} order(s) marked {verb}.")
        if failed:
            self.message_user(request, f"{failed} order(s) could not be updated — see above.", level=messages.WARNING)

    @admin.action(description="Accept selected orders")
    def accept_order_action(self, request, queryset):
        self._run_fulfillment_transition(request, queryset, accept_order, "accepted")

    @admin.action(description="Mark selected orders as Packed")
    def mark_packed_action(self, request, queryset):
        self._run_fulfillment_transition(request, queryset, mark_order_packed, "packed")


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = [
        "code",
        "discount_type",
        "discount_value",
        "minimum_order_amount",
        "usage_limit",
        "redemption_count",
        "is_active",
        "valid_from",
        "valid_until",
    ]
    list_filter = ["is_active", "discount_type"]
    search_fields = ["code"]
    readonly_fields = ["created_at", "updated_at"]

    @admin.display(description="Redeemed")
    def redemption_count(self, obj):
        return obj.redemptions.count()


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ["coupon", "user", "order", "discount_amount", "created_at"]
    search_fields = ["coupon__code", "user__email", "order__order_number"]
    autocomplete_fields = ["coupon", "user", "order"]
    readonly_fields = ["created_at", "updated_at"]
