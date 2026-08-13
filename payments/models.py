"""
Gateway-agnostic payment records.

The rest of the app never talks to a payment gateway SDK directly — only
`payments.services.PaymentService` does that, through the
`payments.gateways.PaymentGateway` abstraction. Everything here exists to
keep a complete, append-only audit trail of what happened to every rupee
that moved through checkout, independent of which gateway processed it.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from common.models import TimeStampedModel


class Payment(TimeStampedModel):
    """One payment attempt against an `Order`, through one gateway."""

    class Gateway(models.TextChoices):
        RAZORPAY = "razorpay", "Razorpay"

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        PENDING = "pending", "Pending"
        AUTHORIZED = "authorized", "Authorized"
        CAPTURED = "captured", "Captured"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"

    class Method(models.TextChoices):
        UPI = "upi", "UPI"
        CARD = "card", "Card"
        NETBANKING = "netbanking", "Net banking"
        WALLET = "wallet", "Wallet"
        EMI = "emi", "EMI"
        OTHER = "other", "Other"

    class RefundStatus(models.TextChoices):
        NONE = "none", "No refund"
        PARTIAL = "partial", "Partially refunded"
        FULL = "full", "Fully refunded"

    # Terminal statuses close out a payment attempt for good — once here, no
    # further gateway event should move it anywhere else. Used to decide
    # whether a duplicate/late webhook delivery is a no-op.
    TERMINAL_STATUSES = {Status.CAPTURED, Status.FAILED, Status.CANCELLED, Status.REFUNDED}

    order = models.ForeignKey("cart.Order", on_delete=models.PROTECT, related_name="payments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")

    gateway = models.CharField(max_length=20, choices=Gateway.choices, default=Gateway.RAZORPAY)
    gateway_order_id = models.CharField(max_length=100, blank=True, db_index=True)
    gateway_payment_id = models.CharField(max_length=100, blank=True, db_index=True)
    signature = models.CharField(max_length=512, blank=True)

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    method = models.CharField(max_length=20, choices=Method.choices, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED)

    transaction_reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Gateway's bank-facing reference (UTR/ARN/RRN) for this payment, if any.",
    )
    failure_reason = models.CharField(max_length=255, blank=True)

    refunded_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    refund_status = models.CharField(max_length=10, choices=RefundStatus.choices, default=RefundStatus.NONE)

    captured_at = models.DateTimeField(null=True, blank=True)

    raw_response = models.JSONField(
        default=dict,
        blank=True,
        help_text="Most recent raw gateway payload received for this payment.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        identifier = self.gateway_payment_id or self.gateway_order_id or str(self.pk)
        return f"Payment {identifier} ({self.get_status_display()})"

    @property
    def is_captured(self):
        return self.status == self.Status.CAPTURED

    @property
    def is_terminal(self):
        return self.status in self.TERMINAL_STATUSES

    @property
    def remaining_refundable(self):
        return self.amount - self.refunded_amount

    @property
    def is_refundable(self):
        return self.status in (self.Status.CAPTURED, self.Status.PARTIALLY_REFUNDED) and self.remaining_refundable > 0


class PaymentEvent(TimeStampedModel):
    """
    Append-only log of every state transition or notable action on a
    `Payment` — client-side verification, webhook deliveries, admin
    actions. Never mutated or deleted, so it doubles as the audit trail
    required for payment reconciliation and fraud review.
    """

    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(
        max_length=50,
        help_text="e.g. 'created', 'client_verified', 'webhook:payment.captured', 'refund_initiated'.",
    )
    previous_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.event_type} on payment {self.payment_id}"


class Refund(TimeStampedModel):
    """One refund (full or partial) issued against a captured `Payment`."""

    class Status(models.TextChoices):
        INITIATED = "initiated", "Initiated"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    gateway_refund_id = models.CharField(max_length=100, blank=True, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INITIATED)
    reason = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Staff member who initiated this refund from the admin, if applicable.",
    )
    return_request = models.ForeignKey(
        "returns.ReturnRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="refunds",
        help_text="Set when this refund was initiated from a return request, rather than a standalone refund.",
    )
    raw_response = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Refund of {self.amount} for payment {self.payment_id} ({self.status})"


class WebhookEvent(TimeStampedModel):
    """
    Records every webhook delivery so duplicate deliveries — which every
    gateway's docs warn will happen — are detected and skipped rather than
    re-applied. `dedupe_key` is the gateway's own delivery id when one is
    supplied (Razorpay sends `X-Razorpay-Event-Id`); otherwise it falls
    back to a hash of the payload so at-least-once delivery still can't
    double-apply an event (see `payments.gateways.razorpay.RazorpayGateway`).
    """

    gateway = models.CharField(max_length=20, choices=Payment.Gateway.choices)
    dedupe_key = models.CharField(max_length=150)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["gateway", "dedupe_key"], name="unique_gateway_webhook_event")]

    def __str__(self):
        return f"{self.gateway}:{self.event_type} ({self.dedupe_key})"
