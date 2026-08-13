"""Buyer-facing return-request flow: request a return, cancel one still pending review."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from cart.models import Order

from .models import ReturnRequest
from .services import cancel_return_request as cancel_return_request_service
from .services import remaining_returnable_quantity
from .services import request_return as request_return_service
from .services import update_return_shipment_tracking as update_return_shipment_tracking_service


@login_required
def request_return(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    items = [
        {"order_item": item, "remaining": remaining_returnable_quantity(item)}
        for item in order.items.select_related("product", "variant")
    ]
    items = [entry for entry in items if entry["remaining"] > 0]

    if request.method == "POST":
        reason = request.POST.get("reason", "")
        comments = request.POST.get("comments", "").strip()
        lines_data = []
        for entry in items:
            item = entry["order_item"]
            raw_quantity = request.POST.get(f"quantity_{item.pk}", "").strip()
            try:
                quantity = int(raw_quantity)
            except ValueError:
                continue
            if quantity <= 0:
                continue
            lines_data.append({"order_item": item, "quantity": quantity})

        try:
            request_return_service(order, lines_data, reason=reason, comments=comments, requested_by=request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("returns:request_return", order_number=order.order_number)

        messages.success(request, "Your return request has been submitted.")
        return redirect("cart:order_confirmation", order_number=order.order_number)

    context = {
        "order": order,
        "items": items,
        "reasons": ReturnRequest.Reason.choices,
    }
    return render(request, "returns/request_return.html", context)


@login_required
@require_POST
def cancel_return_request(request, pk):
    return_request = get_object_or_404(ReturnRequest, pk=pk, user=request.user)
    try:
        cancel_return_request_service(return_request, cancelled_by=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Your return request has been cancelled.")
    return redirect("cart:order_confirmation", order_number=return_request.order.order_number)


@login_required
@require_POST
def update_return_shipment(request, pk):
    return_request = get_object_or_404(ReturnRequest, pk=pk, user=request.user)
    carrier = request.POST.get("carrier", "")
    tracking_number = request.POST.get("tracking_number", "").strip()
    tracking_url = request.POST.get("tracking_url", "").strip()
    try:
        update_return_shipment_tracking_service(
            return_request, carrier=carrier, tracking_number=tracking_number, tracking_url=tracking_url
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Your return shipment tracking has been saved.")
    return redirect("cart:order_confirmation", order_number=return_request.order.order_number)
