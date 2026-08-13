"""
Post-delivery return requests. Mirrors the `cart.Order`/`OrderItem` and
`inventory.PurchaseOrder`/`PurchaseOrderLine` pattern: a `ReturnRequest`
header with `ReturnRequestLine` line items pointing back at the specific
`OrderItem`s and quantities being returned (a return doesn't have to
cover a whole order). See `returns/services.py` for the workflow that
moves a request through its states.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from common.models import TimeStampedModel
from fulfillment.models import Shipment


class ReturnRequest(TimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        RECEIVED = "received", "Received"
        REFUND_INITIATED = "refund_initiated", "Refund initiated"
        REFUNDED = "refunded", "Refunded"
        CANCELLED = "cancelled", "Cancelled"

    class Reason(models.TextChoices):
        DEFECTIVE = "defective", "Item is defective/damaged"
        WRONG_ITEM = "wrong_item", "Received the wrong item"
        NOT_AS_DESCRIBED = "not_as_described", "Not as described"
        NO_LONGER_NEEDED = "no_longer_needed", "No longer needed"
        OTHER = "other", "Other"

    # Only a REQUESTED return can be cancelled by the buyer themselves —
    # once staff have acted on it (approved/rejected) or it's moved past
    # that, cancellation isn't a self-service action anymore.
    CANCELLABLE_STATUSES = {Status.REQUESTED}

    order = models.ForeignKey("cart.Order", on_delete=models.PROTECT, related_name="return_requests")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="return_requests")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    comments = models.TextField(blank=True, help_text="The buyer's own description of the issue.")
    staff_notes = models.TextField(blank=True, help_text="Internal notes — e.g. why a request was rejected.")

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Staff member who approved/rejected this request.",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Return for {self.order.order_number} ({self.get_status_display()})"

    @property
    def suggested_refund_amount(self):
        """
        A starting point for the refund amount, computed from the
        returned lines' frozen unit prices — not account for any
        order-level discount/coupon proration. Staff review and can
        adjust this in the refund form before confirming, same as any
        other refund.
        """
        return sum(
            (line.order_item.unit_price * line.quantity for line in self.lines.select_related("order_item")),
            Decimal("0.00"),
        )

    @property
    def refundable_payment(self):
        """The order's most recent captured/partially-refunded payment — what this return's refund applies against."""
        from payments.models import Payment

        return (
            self.order.payments.filter(status__in=[Payment.Status.CAPTURED, Payment.Status.PARTIALLY_REFUNDED])
            .order_by("-created_at")
            .first()
        )


class ReturnRequestLine(TimeStampedModel):
    return_request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name="lines")
    order_item = models.ForeignKey("cart.OrderItem", on_delete=models.PROTECT, related_name="return_request_lines")
    quantity = models.PositiveIntegerField()

    # Set together when staff process receipt (see
    # returns.services.mark_return_received) — not chosen up front, since
    # the decision depends on the item's actual condition on arrival.
    restocked = models.BooleanField(default=False)
    condition_note = models.CharField(
        max_length=255, blank=True, help_text='Why an item wasn\'t restocked, e.g. "damaged in transit".'
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantity} x {self.order_item.product_title} for {self.return_request}"


class ReturnShipment(TimeStampedModel):
    """
    Tracks the buyer's parcel shipping the returned item(s) back to the
    warehouse — the reverse-pickup counterpart to `fulfillment.Shipment`.
    Created in AWAITING_DISPATCH as soon as a `ReturnRequest` is approved
    (see `returns.services.approve_return`); the buyer (or staff, if a
    pickup was arranged for them) fills in carrier/tracking once it's
    actually shipped (`returns.services.update_return_shipment_tracking`).
    Flipped to DELIVERED when staff record physical receipt
    (`returns.services.mark_return_received`) — that step *is* the
    delivery event, so there's no separate manual "mark delivered" here.
    """

    class Status(models.TextChoices):
        AWAITING_DISPATCH = "awaiting_dispatch", "Awaiting dispatch"
        SHIPPED = "shipped", "Shipped"
        IN_TRANSIT = "in_transit", "In transit"
        DELIVERED = "delivered", "Delivered"
        LOST = "lost", "Lost"

    return_request = models.OneToOneField(ReturnRequest, on_delete=models.CASCADE, related_name="return_shipment")
    carrier = models.CharField(max_length=20, choices=Shipment.Carrier.choices, default=Shipment.Carrier.SELF)
    tracking_number = models.CharField(max_length=64, blank=True)
    tracking_url = models.URLField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AWAITING_DISPATCH)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Return shipment for {self.return_request} ({self.get_status_display()})"
