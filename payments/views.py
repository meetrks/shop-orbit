"""
The only two HTTP entry points into the payments app: the client-side
verification callback (called by the checkout page's JS right after
Razorpay Checkout closes) and the Razorpay webhook receiver. Everything
else — creating a payment, initiating a refund — is called directly from
`cart.views`/the admin via `payments.services.PaymentService`, not HTTP.
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from cart.models import Order

from .models import Payment
from .services import PaymentService

logger = logging.getLogger(__name__)


@login_required
@require_POST
def verify_payment(request, order_number):
    """
    Called by the checkout page's JS immediately after Razorpay Checkout
    reports success, so the buyer gets an instant redirect instead of
    waiting on the webhook. The webhook remains the authoritative source
    of truth — if this call is skipped entirely (tab closed mid-flow) or
    disagrees with what the gateway says, the webhook still confirms (or
    fails) the order correctly on its own.
    """
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"success": False, "error": "Malformed request."}, status=400)

    gateway_order_id = data.get("razorpay_order_id", "")
    gateway_payment_id = data.get("razorpay_payment_id", "")
    signature = data.get("razorpay_signature", "")

    payment = Payment.objects.filter(order=order, gateway_order_id=gateway_order_id).order_by("-created_at").first()
    if payment is None:
        return JsonResponse({"success": False, "error": "No matching payment attempt found."}, status=404)

    captured = PaymentService().verify_client_callback(
        payment,
        gateway_payment_id=gateway_payment_id,
        gateway_order_id=gateway_order_id,
        signature=signature,
    )
    redirect_url = reverse("cart:payment_success" if captured else "cart:payment_failed", args=[order.order_number])
    return JsonResponse({"success": captured, "redirect_url": redirect_url})


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """
    Razorpay POSTs here for every payment/refund/order event. No login is
    possible (or expected) on a server-to-server webhook — the signature
    check inside `process_webhook` is what stands in for authentication,
    so CSRF exemption here doesn't weaken anything a CSRF token would have
    protected anyway.
    """
    try:
        handled = PaymentService("razorpay").process_webhook(raw_body=request.body, headers=request.headers)
    except Exception:
        logger.exception("Error while processing a Razorpay webhook delivery.")
        return HttpResponse(status=500)
    if not handled:
        return HttpResponse(status=400)
    return HttpResponse(status=200)
