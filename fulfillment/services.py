"""
Generation of fulfillment records once payment is captured. Only ever
called from `payments.pipeline.on_payment_captured` — never directly from
a view, so an invoice/packing slip is never generated for an order whose
payment hasn't actually cleared.
"""

from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from .couriers import delhivery
from .models import Invoice, InvoiceNumberSequence, PackingSlip, PincodeServiceability, Shipment
from .pdf import build_invoice_pdf, build_packing_slip_pdf


def _current_financial_year():
    """Indian financial year runs 1 Apr - 31 Mar, formatted e.g. "2025-26"."""
    today = timezone.localdate()
    start_year = today.year if today.month >= 4 else today.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def _next_invoice_number():
    financial_year = _current_financial_year()
    with transaction.atomic():
        sequence, _created = InvoiceNumberSequence.objects.select_for_update().get_or_create(
            financial_year=financial_year
        )
        sequence.last_number += 1
        sequence.save(update_fields=["last_number"])
        number = sequence.last_number
    return f"{settings.INVOICE_SERIES_PREFIX}/{financial_year}/{number:06d}"


def generate_invoice(order):
    """
    Creates (or returns the existing) GST invoice for `order`. Idempotent:
    safe to call again for an order that already has one — e.g. if both a
    webhook delivery and the client-side verification race to run the
    post-payment pipeline for the same order.
    """
    existing = Invoice.objects.filter(order=order).first()
    if existing:
        return existing

    items = list(order.items.all())
    taxable_amount = sum((item.taxable_value for item in items), Decimal("0.00"))
    total_tax = sum((item.tax_amount for item in items), Decimal("0.00"))

    buyer_state = (order.shipping_state or "").strip()
    seller_state = (settings.COMPANY_STATE or "").strip()
    is_interstate = bool(seller_state) and buyer_state.lower() != seller_state.lower()

    if is_interstate:
        igst_amount = total_tax
        cgst_amount = sgst_amount = Decimal("0.00")
    else:
        igst_amount = Decimal("0.00")
        cgst_amount = sgst_amount = (total_tax / 2).quantize(Decimal("0.01"))

    invoice = Invoice.objects.create(
        order=order,
        invoice_number=_next_invoice_number(),
        taxable_amount=taxable_amount,
        cgst_amount=cgst_amount,
        sgst_amount=sgst_amount,
        igst_amount=igst_amount,
        total_tax=total_tax,
        total_amount=order.total_amount,
        buyer_state=buyer_state,
        seller_state=seller_state,
    )
    pdf_bytes = build_invoice_pdf(order, invoice)
    invoice.pdf.save(f"{invoice.invoice_number.replace('/', '-')}.pdf", ContentFile(pdf_bytes), save=True)
    return invoice


def generate_packing_slip(order):
    """Creates (or returns the existing) packing slip for `order`. Idempotent, same reasoning as `generate_invoice`."""
    existing = PackingSlip.objects.filter(order=order).first()
    if existing:
        return existing

    packing_slip = PackingSlip.objects.create(order=order)
    pdf_bytes = build_packing_slip_pdf(order)
    packing_slip.pdf.save(f"packing-slip-{order.order_number}.pdf", ContentFile(pdf_bytes), save=True)
    return packing_slip


def create_shipment(order):
    """Creates (or returns the existing) shipment record for `order`, in PENDING status awaiting carrier assignment."""
    shipment, _created = Shipment.objects.get_or_create(order=order)
    return shipment


def assign_delhivery_waybill(shipment, *, actor=None):
    """
    Manifests `shipment`'s order with Delhivery and records the returned
    waybill as this shipment's tracking number. Raises
    `delhivery.DelhiveryAPIError` (including when DELHIVERY_API_TOKEN
    isn't configured) — the caller (an admin action) is responsible for
    surfacing that to the staff member who triggered it.
    """
    waybill = delhivery.create_waybill(shipment.order)

    shipment.carrier = Shipment.Carrier.DELHIVERY
    shipment.tracking_number = waybill
    shipment.tracking_url = f"https://www.delhivery.com/track-v2/package/{waybill}"
    if actor is not None:
        shipment._status_change_actor = actor
        shipment._status_change_reason = "Waybill assigned via Delhivery API"
    shipment.save()
    return shipment


def sync_shipment_tracking(shipment, *, actor=None):
    """
    Polls Delhivery for `shipment`'s current status and updates it if
    changed. A no-op (returns False) if the shipment has no Delhivery
    waybill yet, or if the tracking response's status isn't one we
    recognize — see `delhivery.map_tracking_status`. Returns True if the
    shipment's status was updated.
    """
    if shipment.carrier != Shipment.Carrier.DELHIVERY or not shipment.tracking_number:
        return False

    tracking_response = delhivery.track(shipment.tracking_number)
    new_status = delhivery.map_tracking_status(tracking_response)
    if new_status is None or new_status == shipment.status:
        return False

    shipment.status = new_status
    if new_status == Shipment.Status.PICKED_UP and not shipment.shipped_at:
        shipment.shipped_at = timezone.now()
    elif new_status == Shipment.Status.DELIVERED and not shipment.delivered_at:
        shipment.delivered_at = timezone.now()
    if actor is not None:
        shipment._status_change_actor = actor
    shipment._status_change_reason = "Synced from Delhivery tracking API"
    shipment.save()
    return True


def is_pincode_serviceable(postal_code):
    """
    A postal code is serviceable unless explicitly marked otherwise —
    see `PincodeServiceability`'s docstring for why this is a blocklist,
    not a full allowlist. Returns the blocking row's note (or a generic
    message) via `pincode_block_reason` instead if the caller wants to
    surface why.
    """
    return not PincodeServiceability.objects.filter(postal_code=postal_code, is_serviceable=False).exists()


def pincode_block_reason(postal_code):
    """The note explaining why `postal_code` is blocked, or a generic fallback. Only call when already blocked."""
    row = PincodeServiceability.objects.filter(postal_code=postal_code, is_serviceable=False).first()
    if row and row.note:
        return row.note
    return "We currently don't deliver to this pincode."
