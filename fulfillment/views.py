"""Download views for GST invoices and packing slips."""

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404

from cart.models import Order

from .models import Invoice, PackingSlip


@login_required
def invoice_download(request, order_number):
    """The buyer who placed the order, or any staff member, can download its invoice."""
    order = get_object_or_404(Order, order_number=order_number)
    if order.user_id != request.user.id and not request.user.is_staff:
        return HttpResponseForbidden("You don't have access to this invoice.")
    invoice = get_object_or_404(Invoice, order=order)
    return FileResponse(invoice.pdf.open("rb"), filename=f"{invoice.invoice_number.replace('/', '-')}.pdf")


@login_required
def packing_slip_download(request, order_number):
    """Packing slips are a fulfillment document — staff only, not shown to the buyer."""
    if not request.user.is_staff:
        return HttpResponseForbidden("You don't have access to this document.")
    order = get_object_or_404(Order, order_number=order_number)
    packing_slip = get_object_or_404(PackingSlip, order=order)
    return FileResponse(packing_slip.pdf.open("rb"), filename=f"packing-slip-{order.order_number}.pdf")
