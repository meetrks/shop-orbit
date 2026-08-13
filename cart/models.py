"""
Cart, order, and order-item models.

Checkout freezes the cart into an `Order` (`AWAITING_PAYMENT`) before any
money moves, then hands off to `payments.services.PaymentService` to create
a Razorpay order and take payment. `Order.status` only ever advances to
`PAYMENT_CONFIRMED` once `payments` reports a captured payment — see
`payments/pipeline.py` for what that triggers (stock reduction, GST
invoice, packing slip, shipment record, and buyer notification).
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from accounts.models import phone_number_validator
from catalog.models import Product, ProductVariant
from common.models import TimeStampedModel


class Cart(TimeStampedModel):
    """A single persistent cart belonging to one user."""

    AUTO_PROMO_MIN_ITEMS = 2
    AUTO_PROMO_PERCENT = Decimal("10")
    AUTO_PROMO_MAX_DISCOUNT = Decimal("50.00")

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart")

    def __str__(self):
        return f"Cart for {self.user.email}"

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def subtotal(self):
        return sum((item.line_total for item in self.items.all()), Decimal("0.00"))

    @property
    def auto_promo_discount(self):
        """
        Automatic "buy more, save more" discount: 10% off, capped at Rs 50,
        applied whenever the cart holds 2 or more items total. Distinct
        from Coupon — no code needed, and it's offered as an alternative
        to any coupon the buyer enters rather than stacked with it.
        """
        if self.total_items < self.AUTO_PROMO_MIN_ITEMS:
            return Decimal("0.00")
        discount = self.subtotal * (self.AUTO_PROMO_PERCENT / Decimal("100"))
        return min(discount, self.AUTO_PROMO_MAX_DISCOUNT).quantize(Decimal("0.01"))


class Wishlist(TimeStampedModel):
    """A single persistent wishlist belonging to one user."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wishlist")

    def __str__(self):
        return f"Wishlist for {self.user.email}"


class WishlistItem(TimeStampedModel):
    """One saved product on a user's wishlist. Product-level, not variant-level."""

    wishlist = models.ForeignKey(Wishlist, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="wishlisted_by")

    class Meta:
        unique_together = [("wishlist", "product")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product.title} in {self.wishlist.user.email}'s wishlist"


class CartItem(TimeStampedModel):
    """A single product (optionally, one variant of it) line within a user's cart."""

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="cart_items")
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cart_items",
    )
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        unique_together = [("cart", "product", "variant")]
        ordering = ["-created_at"]

    def __str__(self):
        suffix = f" ({self.variant.label})" if self.variant_id else ""
        return f"{self.quantity} x {self.product.title}{suffix}"

    @property
    def unit_price(self):
        return self.variant.display_price if self.variant_id else self.product.display_price

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    @property
    def available_stock(self):
        return self.variant.available_to_sell if self.variant_id else self.product.available_to_sell


class Coupon(TimeStampedModel):
    """A discount code redeemable at checkout."""

    class DiscountType(models.TextChoices):
        PERCENT = "percent", "Percentage off"
        FLAT = "flat", "Flat amount off"

    code = models.CharField(max_length=32, unique=True)
    discount_type = models.CharField(max_length=10, choices=DiscountType.choices, default=DiscountType.PERCENT)
    discount_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="A percentage (e.g. 10 for 10%) or a flat rupee amount, depending on discount type.",
    )
    max_discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Caps the discount for percentage coupons. Leave blank for no cap. Ignored for flat coupons.",
    )
    minimum_order_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(
        null=True, blank=True, help_text="Total redemptions allowed across all buyers. Leave blank for unlimited."
    )
    per_user_limit = models.PositiveIntegerField(
        default=1, help_text="Max redemptions per buyer. Set to 0 for unlimited."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def calculate_discount(self, subtotal):
        if self.discount_type == self.DiscountType.PERCENT:
            discount = subtotal * (self.discount_value / Decimal("100"))
            if self.max_discount_amount is not None:
                discount = min(discount, self.max_discount_amount)
        else:
            discount = self.discount_value
        return min(discount, subtotal).quantize(Decimal("0.01"))

    def validate_for(self, user, subtotal):
        """Returns an error message string, or "" if the coupon can be applied."""
        now = timezone.now()
        if not self.is_active:
            return "This coupon is no longer active."
        if self.valid_from and now < self.valid_from:
            return "This coupon isn't active yet."
        if self.valid_until and now > self.valid_until:
            return "This coupon has expired."
        if subtotal < self.minimum_order_amount:
            return f"This coupon requires a minimum order of ₹{self.minimum_order_amount}."
        if self.usage_limit is not None and self.redemptions.count() >= self.usage_limit:
            return "This coupon has reached its usage limit."
        if self.per_user_limit and self.redemptions.filter(user=user).count() >= self.per_user_limit:
            return "You've already used this coupon."
        return ""


class Order(TimeStampedModel):
    """
    A checked-out cart snapshot. Payment is handled by the `payments` app
    (see `payments.services.PaymentService`) — this model only tracks
    fulfillment status. It starts `AWAITING_PAYMENT` the moment checkout
    validates and freezes the cart, moves to `PAYMENT_CONFIRMED`
    automatically once `payments` reports a captured payment (via webhook
    or client-side verification, whichever lands first), and never
    reaches `SHIPPED` on an unpaid order.
    """

    class Status(models.TextChoices):
        AWAITING_PAYMENT = "awaiting_confirmation", "Awaiting payment"
        PAYMENT_CONFIRMED = "payment_confirmed", "Payment confirmed"
        PAYMENT_FAILED = "payment_failed", "Payment failed"
        EXPIRED = "expired", "Payment window expired"
        # Internal fulfillment-prep stages, staff-driven (see
        # cart.services.accept_order/mark_order_packed), sitting between
        # payment capture and handing the order to a courier. A paid order
        # sits in PAYMENT_CONFIRMED as its own implicit queue until staff
        # accept or cancel it — there's no separate "pending" holding
        # status. SHIPPED still only ever gets set automatically, by
        # fulfillment.signals once a Shipment reports picked up/in
        # transit/out for delivery — packed is the last manual stage.
        ACCEPTED = "accepted", "Accepted"
        PACKED = "packed", "Packed"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"

    # Statuses a buyer or staff member can cancel from — anything already
    # shipped, already cancelled/refunded, or already expired is final.
    CANCELLABLE_STATUSES = {
        Status.AWAITING_PAYMENT,
        Status.PAYMENT_FAILED,
        Status.PAYMENT_CONFIRMED,
        Status.ACCEPTED,
        Status.PACKED,
    }

    # Every status only reachable once a payment has actually been
    # captured — used both for "is this real revenue" reporting (store
    # stats, the revenue chart, review eligibility) and to tell
    # cart.services.cancel_order whether cancelling needs to restore real
    # stock (already decremented on capture) rather than just release a
    # checkout reservation. Deliberately excludes CANCELLED/REFUNDED/
    # PARTIALLY_REFUNDED — those are reached from many different prior
    # statuses (paid or not) and by the time an order is in one of them,
    # its revenue has already been reversed or is being tracked separately.
    PAID_STATUSES = {
        Status.PAYMENT_CONFIRMED,
        Status.ACCEPTED,
        Status.PACKED,
        Status.SHIPPED,
        Status.DELIVERED,
    }

    order_number = models.CharField(max_length=20, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders")
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.AWAITING_PAYMENT)

    shipping_full_name = models.CharField(max_length=150)
    shipping_phone_number = models.CharField(max_length=16, validators=[phone_number_validator])
    shipping_address_line1 = models.CharField(max_length=255)
    shipping_address_line2 = models.CharField(max_length=255, blank=True)
    shipping_city = models.CharField(max_length=100)
    shipping_state = models.CharField(max_length=100)
    shipping_postal_code = models.CharField(max_length=12)
    shipping_country = models.CharField(max_length=100, default="India")

    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    coupon_code = models.CharField(
        max_length=32,
        blank=True,
        help_text="Snapshot of the coupon code applied, if any (kept even if the coupon is later deleted).",
    )

    upi_payment_reference = models.CharField(
        max_length=64,
        blank=True,
        help_text="UTR / transaction reference number the buyer entered at checkout.",
    )
    customer_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order {self.order_number} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

    def _generate_order_number(self):
        from django.utils.crypto import get_random_string

        while True:
            candidate = settings.ORDER_NUMBER_PREFIX + get_random_string(8, allowed_chars="0123456789")
            if not Order.objects.filter(order_number=candidate).exists():
                return candidate

    @property
    def total_quantity(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def total_amount(self):
        return self.subtotal_amount - self.discount_amount


class OrderStatusHistory(TimeStampedModel):
    """
    Append-only audit trail of every status transition an `Order` goes
    through — mirrors `payments.PaymentEvent`. Written unconditionally by
    `cart.signals` on every save that changes `status`, regardless of
    whether the buyer-facing status-change email fired for that
    transition (see `Order._skip_status_email`) — the audit trail must be
    complete even when a richer, purpose-built email superseded the
    generic one.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    previous_status = models.CharField(max_length=25, blank=True)
    new_status = models.CharField(max_length=25)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=(
            "Staff member or buyer who triggered this change directly. Blank means "
            "system-triggered (payment webhook, expiry sweep, shipment update)."
        ),
    )
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "Order status history"

    def __str__(self):
        return f"{self.order.order_number}: {self.previous_status} -> {self.new_status}"


class OrderItem(TimeStampedModel):
    """A frozen snapshot of one product line at the moment of checkout."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, related_name="order_items")
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
    )
    product_title = models.CharField(max_length=200)
    product_sku = models.CharField(max_length=64)
    variant_label = models.CharField(max_length=200, blank=True)
    variant_sku = models.CharField(max_length=64, blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    # Frozen at checkout time from Product.hsn_code/gst_rate, same as every
    # other field on this model — a later change to the product's tax
    # classification must never alter an already-placed order's invoice.
    hsn_code = models.CharField(max_length=8, blank=True)
    gst_rate = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["id"]

    def __str__(self):
        suffix = f" ({self.variant_label})" if self.variant_label else ""
        return f"{self.quantity} x {self.product_title}{suffix}"

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    @property
    def tax_amount(self):
        """
        GST portion of `line_total`, back-calculated because `unit_price` is
        already tax-inclusive (see `Product.display_price`): for a
        tax-inclusive price P and rate r%, tax = P * r / (100 + r).
        """
        if not self.gst_rate:
            return Decimal("0.00")
        rate = self.gst_rate / Decimal("100")
        return (self.line_total * rate / (1 + rate)).quantize(Decimal("0.01"))

    @property
    def taxable_value(self):
        """`line_total` net of GST — the base value GST is calculated on."""
        return self.line_total - self.tax_amount


class CouponRedemption(TimeStampedModel):
    """One use of a Coupon, tied to the order it was applied to. Enforces per-user/global usage limits."""

    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="redemptions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="coupon_redemptions")
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="coupon_redemption")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.coupon.code} on {self.order.order_number}"
