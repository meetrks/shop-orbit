"""
Stock movement ledger, plus supplier/purchase-order management. Every
change to a product/variant's stock — reserving it for an in-flight
checkout, releasing that reservation, converting it to a sale on payment
capture, restocking, or a manual correction — is recorded to
`StockMovement`, in addition to updating the live `stock_count`/
`reserved_count` counters on `catalog.Product`/`ProductVariant`. See
`inventory/services.py` for the only code that's meant to write these.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from common.models import TimeStampedModel


class StockMovement(TimeStampedModel):
    class MovementType(models.TextChoices):
        RESERVED = "reserved", "Reserved for checkout"
        RELEASED = "released", "Reservation released"
        SOLD = "sold", "Sold (payment captured)"
        RESTOCKED = "restocked", "Restocked"
        RESTORED = "restored", "Restored (order cancelled after payment)"
        RETURNED = "returned", "Returned by customer"
        ADJUSTED = "adjusted", "Manual adjustment"

    product = models.ForeignKey(
        "catalog.Product", on_delete=models.SET_NULL, null=True, related_name="stock_movements"
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)

    # Most movement types only touch one of these two — e.g. RESERVED only
    # moves reserved_delta, RESTOCKED only moves stock_delta — but SOLD
    # moves both (a reserved unit leaves both the reserved pool and the
    # total-on-hand pool at once). Signed so the ledger sums to the current
    # counters at any point in time.
    stock_delta = models.IntegerField(default=0)
    reserved_delta = models.IntegerField(default=0)

    order = models.ForeignKey(
        "cart.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_movements"
    )
    purchase_order = models.ForeignKey(
        "inventory.PurchaseOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )
    return_request = models.ForeignKey(
        "returns.ReturnRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Staff member who made a manual adjustment/restock. Blank for system-triggered movements.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        target = self.variant or self.product
        return (
            f"{self.get_movement_type_display()}: {target} "
            f"(stock {self.stock_delta:+d}, reserved {self.reserved_delta:+d})"
        )


class Supplier(TimeStampedModel):
    """A vendor products can be restocked from via a PurchaseOrder."""

    name = models.CharField(max_length=150, unique=True)
    contact_person = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=16, blank=True)
    email = models.EmailField(blank=True)
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=12, blank=True)
    country = models.CharField(max_length=100, default="India", blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PurchaseOrderSequence(models.Model):
    """One row, handing out gapless sequential PO numbers — same pattern as `fulfillment.InvoiceNumberSequence`."""

    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Purchase order sequence (last: {self.last_number})"


class PurchaseOrder(TimeStampedModel):
    """A restock order placed with a `Supplier`. Lines are received individually, in full or in part."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ORDERED = "ordered", "Ordered"
        PARTIALLY_RECEIVED = "partially_received", "Partially received"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    po_number = models.CharField(max_length=20, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    expected_delivery_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PO {self.po_number} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.po_number:
            self.po_number = self._generate_po_number()
        super().save(*args, **kwargs)

    def _generate_po_number(self):
        from django.db import transaction

        with transaction.atomic():
            sequence, _created = PurchaseOrderSequence.objects.select_for_update().get_or_create(pk=1)
            sequence.last_number += 1
            sequence.save(update_fields=["last_number"])
            number = sequence.last_number
        return f"PO-{number:06d}"

    @property
    def total_cost(self):
        return sum((line.line_total for line in self.lines.all()), Decimal("0.00"))

    @property
    def is_fully_received(self):
        lines = list(self.lines.all())
        return bool(lines) and all(line.is_fully_received for line in lines)

    @property
    def is_partially_received(self):
        return any(line.quantity_received > 0 for line in self.lines.all()) and not self.is_fully_received


class PurchaseOrderLine(TimeStampedModel):
    """One product/variant line within a `PurchaseOrder`."""

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="purchase_order_lines")
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="purchase_order_lines",
    )
    quantity_ordered = models.PositiveIntegerField()
    quantity_received = models.PositiveIntegerField(default=0)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["id"]

    def __str__(self):
        target = self.variant or self.product
        return f"{self.quantity_ordered} x {target} for {self.purchase_order.po_number}"

    @property
    def line_total(self):
        return self.unit_cost * self.quantity_ordered

    @property
    def quantity_remaining(self):
        return self.quantity_ordered - self.quantity_received

    @property
    def is_fully_received(self):
        return self.quantity_received >= self.quantity_ordered
