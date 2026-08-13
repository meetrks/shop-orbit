"""
Post-payment fulfillment records: the GST invoice, packing slip, and
shipment tracking generated once an order's payment is captured (see
`payments.pipeline.on_payment_captured`). Kept separate from `payments`
because these are about moving physical goods, not money.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from common.models import TimeStampedModel


class InvoiceNumberSequence(models.Model):
    """
    One row per Indian financial year, handing out gapless sequential
    invoice numbers. Kept as its own counter (rather than
    `Invoice.objects.count() + 1`) so a corrected or voided invoice can
    never cause a number to be skipped or reused.
    """

    financial_year = models.CharField(max_length=9, unique=True, help_text='e.g. "2025-26".')
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Invoice sequence for FY {self.financial_year}"


class Invoice(TimeStampedModel):
    """
    A GST tax invoice for one `Order`, issued by the platform company
    (`COMPANY_*` settings) as merchant of record. This storefront doesn't
    implement per-seller marketplace GST invoicing (Section 52 TCS,
    seller-wise GSTINs) — every invoice is issued by the single legal
    entity running the site, which matches how `COMPANY_GST_NUMBER` is
    already modeled as one site-wide setting rather than a per-seller field.

    Generated exactly once, immediately after payment capture, from
    `OrderItem`'s own frozen `hsn_code`/`gst_rate` snapshot — never
    regenerated even if the company's tax settings change later.
    """

    order = models.OneToOneField("cart.Order", on_delete=models.PROTECT, related_name="invoice")
    invoice_number = models.CharField(max_length=32, unique=True, editable=False)

    taxable_amount = models.DecimalField(max_digits=10, decimal_places=2)
    cgst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    sgst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    igst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total_tax = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    buyer_state = models.CharField(
        max_length=100, help_text="Snapshot of the buyer's shipping state, used to decide CGST+SGST vs IGST."
    )
    seller_state = models.CharField(max_length=100, help_text="Snapshot of COMPANY_STATE at generation time.")

    pdf = models.FileField(upload_to="invoices/%Y/%m/")
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invoice {self.invoice_number} for {self.order.order_number}"

    @property
    def is_interstate(self):
        return self.igst_amount > 0


class PackingSlip(TimeStampedModel):
    """A warehouse/seller-facing pick-and-pack document, generated once per order."""

    order = models.OneToOneField("cart.Order", on_delete=models.PROTECT, related_name="packing_slip")
    pdf = models.FileField(upload_to="packing_slips/%Y/%m/")
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Packing slip for {self.order.order_number}"


class Shipment(TimeStampedModel):
    """
    Tracks one order's physical fulfillment after payment. Created in
    `PENDING` status as soon as payment is confirmed; staff assign a
    carrier and paste in the tracking link/number once the courier has
    picked it up (the exact tracking-URL format differs per courier and
    account, so it's recorded directly rather than templated from a
    carrier name).
    """

    class Carrier(models.TextChoices):
        DELHIVERY = "delhivery", "Delhivery"
        BLUEDART = "bluedart", "Blue Dart"
        DTDC = "dtdc", "DTDC"
        INDIA_POST = "india_post", "India Post"
        EKART = "ekart", "Ekart"
        SELF = "self", "Self-shipped"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending assignment"
        PICKED_UP = "picked_up", "Picked up"
        IN_TRANSIT = "in_transit", "In transit"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
        DELIVERED = "delivered", "Delivered"
        NDR = "ndr", "Delivery attempt failed"
        RETURNED = "returned", "Returned to seller"
        CANCELLED = "cancelled", "Cancelled"

    order = models.OneToOneField("cart.Order", on_delete=models.PROTECT, related_name="shipment")
    carrier = models.CharField(max_length=20, choices=Carrier.choices, default=Carrier.OTHER)
    tracking_number = models.CharField(max_length=64, blank=True)
    tracking_url = models.URLField(
        blank=True, help_text="Paste the tracking link the courier provides for this shipment."
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Shipment for {self.order.order_number} ({self.get_status_display()})"


class ShipmentStatusHistory(TimeStampedModel):
    """
    Append-only audit trail of every status transition a `Shipment` goes
    through — mirrors `cart.OrderStatusHistory`. Written unconditionally
    by `fulfillment.signals` on every save that changes `status`,
    regardless of whether that save came from an admin action, a direct
    field edit, or future code.
    """

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="status_history")
    previous_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Staff member who triggered this change directly. Blank means system-triggered.",
    )
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "Shipment status history"

    def __str__(self):
        return f"{self.shipment.order.order_number}: {self.previous_status} -> {self.new_status}"


class PincodeServiceability(TimeStampedModel):
    """
    Records postal codes known to be OUT of a carrier's reach — a
    blocklist, not a full allowlist. A postal code with no row here is
    assumed serviceable by default (see `fulfillment.services.
    is_pincode_serviceable`), so checkout isn't broken for the ~19,000
    Indian pincodes staff haven't had reason to review yet. Rows only
    need to exist for the exceptions.
    """

    postal_code = models.CharField(max_length=12, unique=True)
    is_serviceable = models.BooleanField(
        default=False, help_text="Uncheck to block checkout for this pincode. Rows are only added for exceptions."
    )
    note = models.CharField(max_length=255, blank=True, help_text='e.g. "Courier doesn\'t reach this area."')

    class Meta:
        ordering = ["postal_code"]
        verbose_name_plural = "Pincode serviceability"

    def __str__(self):
        return f"{self.postal_code} ({'serviceable' if self.is_serviceable else 'blocked'})"
