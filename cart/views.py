"""
Cart management and checkout views.

Checkout validates the cart (stock, pricing, coupon), freezes it into an
`Order` in `AWAITING_PAYMENT` status, and hands off to
`payments.services.PaymentService` to create a Razorpay order — the buyer
never leaves this site; Razorpay Checkout opens as an in-page modal (see
templates/cart/checkout_payment.html). The order only ever advances to
`PAYMENT_CONFIRMED` once `payments` reports a captured payment, which also
triggers stock reduction, GST invoice/packing-slip generation, and the
buyer's order-confirmation email (see payments/pipeline.py) — none of
that happens here.
"""

import logging
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.models import Address
from accounts.services import create_address
from catalog.models import Product
from fulfillment.models import Shipment
from inventory.services import InsufficientStockError, reserve_stock
from payments.models import Payment
from payments.services import PaymentService
from returns.services import is_order_returnable

from .forms import AddToCartForm, CheckoutForm
from .models import Cart, CartItem, Coupon, CouponRedemption, Order, OrderItem, Wishlist, WishlistItem
from .services import cancel_order as cancel_order_service

logger = logging.getLogger(__name__)


def _get_or_create_cart(user):
    cart, _created = Cart.objects.get_or_create(user=user)
    return cart


def _get_or_create_wishlist(user):
    wishlist, _created = Wishlist.objects.get_or_create(user=user)
    return wishlist


def _out_of_stock_items(cart_items):
    """Cart lines whose quantity now exceeds available stock — re-checked here since the cart can sit a while."""
    return [item for item in cart_items if item.quantity > item.available_stock]


@login_required
def cart_detail(request):
    cart = _get_or_create_cart(request.user)
    context = {"cart": cart, "cart_items": cart.items.select_related("product", "variant")}
    return render(request, "cart/cart_detail.html", context)


@login_required
@require_POST
def add_to_cart(request, product_id):
    product = get_object_or_404(Product, pk=product_id, is_active=True)
    form = AddToCartForm(request.POST)
    quantity = form.cleaned_data["quantity"] if form.is_valid() else 1

    variant = None
    active_variants = product.variants.filter(is_active=True)
    if active_variants.exists():
        variant = get_object_or_404(active_variants, pk=request.POST.get("variant_id"))

    cart = _get_or_create_cart(request.user)
    item, created = CartItem.objects.get_or_create(
        cart=cart, product=product, variant=variant, defaults={"quantity": quantity}
    )
    if not created:
        item.quantity += quantity
        item.save()

    label = f" ({variant.label})" if variant else ""
    messages.success(request, f'"{product.title}{label}" was added to your cart.')
    return redirect(request.META.get("HTTP_REFERER") or "cart:cart_detail")


@login_required
@require_POST
def update_cart_item(request, item_id):
    item = get_object_or_404(CartItem, pk=item_id, cart__user=request.user)
    try:
        quantity = int(request.POST.get("quantity", 1))
    except ValueError:
        quantity = 1

    if quantity <= 0:
        item.delete()
        messages.info(request, "Item removed from your cart.")
    else:
        item.quantity = quantity
        item.save()
        messages.success(request, "Cart updated.")
    return redirect("cart:cart_detail")


@login_required
@require_POST
def remove_cart_item(request, item_id):
    item = get_object_or_404(CartItem, pk=item_id, cart__user=request.user)
    item.delete()
    messages.info(request, "Item removed from your cart.")
    return redirect("cart:cart_detail")


@login_required
def checkout(request):
    cart = _get_or_create_cart(request.user)
    cart_items = list(cart.items.select_related("product", "variant"))

    if not cart_items:
        messages.warning(request, "Your cart is empty.")
        return redirect("cart:cart_detail")

    # The automatic bulk-item promo (10% off, capped at Rs 50, for 2+
    # items) and a manually-entered coupon are offered as alternatives,
    # never stacked — whichever gives the bigger discount is applied
    # automatically.
    auto_discount = cart.auto_promo_discount
    applied_coupon = None
    coupon_preview_error = ""

    if request.method == "POST":
        form = CheckoutForm(request.POST, instance=Order(user=request.user), user=request.user, subtotal=cart.subtotal)
        out_of_stock = _out_of_stock_items(cart_items)
        if out_of_stock and form.is_valid():
            # Re-validated here (not just at add-to-cart time) since a cart
            # can sit for a while before checkout, and stock could have
            # dropped in the meantime — never trust the cart's saved
            # quantities without a final check right before charging.
            for item in out_of_stock:
                label = item.product.title + (f" ({item.variant.label})" if item.variant_id else "")
                messages.error(
                    request,
                    f'Only {item.available_stock} of "{label}" left in stock — please update your cart.',
                )
        elif form.is_valid():
            coupon_discount = form.coupon.calculate_discount(cart.subtotal) if form.coupon else Decimal("0.00")
            use_coupon = form.coupon is not None and coupon_discount >= auto_discount and coupon_discount > 0
            use_promo = not use_coupon and auto_discount > 0

            try:
                with transaction.atomic():
                    order = form.save(commit=False)
                    order.user = request.user
                    order.subtotal_amount = cart.subtotal
                    if use_coupon:
                        order.discount_amount = coupon_discount
                    elif use_promo:
                        order.discount_amount = auto_discount
                        order.coupon_code = ""
                    else:
                        order.discount_amount = Decimal("0.00")
                        order.coupon_code = ""
                    order.save()

                    for cart_item in cart_items:
                        OrderItem.objects.create(
                            order=order,
                            product=cart_item.product,
                            variant=cart_item.variant,
                            product_title=cart_item.product.title,
                            product_sku=cart_item.product.sku,
                            variant_label=cart_item.variant.label if cart_item.variant_id else "",
                            variant_sku=cart_item.variant.sku if cart_item.variant_id else "",
                            unit_price=cart_item.unit_price,
                            quantity=cart_item.quantity,
                            hsn_code=cart_item.product.hsn_code,
                            gst_rate=cart_item.product.gst_rate,
                        )
                    # The authoritative, race-safe stock gate — the
                    # out_of_stock soft check above is just for fast,
                    # friendly feedback before any DB writes. Raising here
                    # rolls back the whole order (nothing's persisted).
                    reserve_stock(order)
                    if use_coupon:
                        CouponRedemption.objects.create(
                            coupon=form.coupon,
                            user=request.user,
                            order=order,
                            discount_amount=order.discount_amount,
                        )
                    if not form.cleaned_data.get("saved_address") and form.cleaned_data.get("save_address"):
                        # The order's own shipping_* fields are the
                        # already-validated source of truth here (not the
                        # raw POST) — same values either way, whether the
                        # buyer typed a fresh address or picked "new" with
                        # stale text left in the fields.
                        create_address(
                            request.user,
                            Address(
                                recipient_name=order.shipping_full_name,
                                phone_number=order.shipping_phone_number,
                                address_line1=order.shipping_address_line1,
                                address_line2=order.shipping_address_line2,
                                city=order.shipping_city,
                                state=order.shipping_state,
                                postal_code=order.shipping_postal_code,
                                country=order.shipping_country,
                            ),
                        )
                    cart.items.all().delete()
            except InsufficientStockError as exc:
                messages.error(request, str(exc))
            else:
                # The Razorpay order (a network call) is deliberately created
                # outside this transaction, by `checkout_payment` below — if it
                # fails, the buyer already has a real Order they can retry
                # payment on, rather than the whole checkout submission 500ing.
                return redirect("cart:checkout_payment", order_number=order.order_number)
        if not out_of_stock:
            # Invalid submission (possibly for reasons unrelated to the coupon) still
            # runs clean_coupon_code, so a valid coupon is preserved for the preview.
            applied_coupon = form.coupon
            # coupon_code is rendered as a hidden field (see CheckoutForm), so
            # crispy never surfaces its errors on the page — flash them instead,
            # otherwise a coupon that became invalid between preview and submit
            # (e.g. someone else just used its last redemption) fails silently.
            for error in form.errors.get("coupon_code", []):
                messages.error(request, error)
            # saved_address is also hand-rendered (see templates/cart/checkout.html),
            # so its errors — e.g. a saved address whose pincode stopped being
            # serviceable since it was saved — need the same flash treatment.
            for error in form.errors.get("saved_address", []):
                messages.error(request, error)
    else:
        default_address = request.user.addresses.filter(is_default=True).first()
        initial = {
            "shipping_full_name": request.user.full_name,
            "shipping_phone_number": request.user.phone_number,
            "saved_address": default_address,
        }
        coupon_code = request.GET.get("coupon_code", "").strip().upper()
        if coupon_code:
            try:
                candidate = Coupon.objects.get(code=coupon_code)
            except Coupon.DoesNotExist:
                coupon_preview_error = "That coupon code isn't valid."
            else:
                error = candidate.validate_for(request.user, cart.subtotal)
                if error:
                    coupon_preview_error = error
                else:
                    applied_coupon = candidate
                    # Only carry a *validated* code into the hidden field that
                    # rides along with the real order submission — an invalid
                    # one would otherwise silently block "Place Order" via a
                    # hidden-field error the shopper can't see.
                    initial["coupon_code"] = coupon_code

        form = CheckoutForm(initial=initial, user=request.user, subtotal=cart.subtotal)

    coupon_discount = applied_coupon.calculate_discount(cart.subtotal) if applied_coupon else Decimal("0.00")
    if applied_coupon and coupon_discount >= auto_discount and coupon_discount > 0:
        discount_amount = coupon_discount
    elif auto_discount > 0:
        discount_amount = auto_discount
    else:
        discount_amount = Decimal("0.00")

    context = {
        "cart": cart,
        "cart_items": cart_items,
        "form": form,
        "saved_addresses": request.user.addresses.all(),
        "applied_coupon": applied_coupon,
        "coupon_preview_error": coupon_preview_error,
        "auto_discount": auto_discount,
        "coupon_discount": coupon_discount,
        "discount_amount": discount_amount,
        "total_payable": cart.subtotal - discount_amount,
    }
    return render(request, "cart/checkout.html", context)


@login_required
def checkout_payment(request, order_number):
    """
    Renders the Razorpay Checkout modal for a freshly-placed order. Reused
    by `payment_retry` for a second attempt on the same order — the
    template only needs a `payment` with a live `gateway_order_id`, not a
    specific code path that created it.
    """
    order = get_object_or_404(Order, order_number=order_number, user=request.user)

    if order.status == Order.Status.PAYMENT_CONFIRMED:
        return redirect("cart:order_confirmation", order_number=order.order_number)
    if order.status not in (Order.Status.AWAITING_PAYMENT, Order.Status.PAYMENT_FAILED):
        return redirect("accounts:profile")

    payment = order.payments.exclude(status__in=Payment.TERMINAL_STATUSES).order_by("-created_at").first()
    if payment is None:
        # No live payment attempt exists yet for this order — either this
        # is the first visit right after checkout, or the previous attempt
        # ended in a terminal state and a retry created this view hit
        # without going through `payment_retry` first. Either way, start
        # one now rather than in the checkout POST, so a Razorpay outage
        # shows a retryable error page instead of 500ing the order submission.
        try:
            payment = PaymentService().create_payment_for_order(order, request.user)
        except Exception:
            logger.exception("Failed to create a Razorpay order for order %s.", order.order_number)
            messages.error(request, "We couldn't start the payment right now. Please try again in a moment.")
            return render(request, "cart/payment_failed.html", {"order": order})

    context = {
        "order": order,
        "payment": payment,
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
        "amount_in_paise": int(payment.amount * 100),
    }
    return render(request, "cart/checkout_payment.html", context)


@login_required
@require_POST
def payment_retry(request, order_number):
    """Starts a fresh payment attempt on an order whose last one failed (or was abandoned)."""
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    if order.status not in (Order.Status.AWAITING_PAYMENT, Order.Status.PAYMENT_FAILED):
        messages.error(request, "This order can't be retried.")
        return redirect("accounts:profile")

    # A retry after a real failure should read as a fresh attempt, not a
    # continuation of the failed one, so the order goes back to
    # AWAITING_PAYMENT before a new Payment is created for it. A failed
    # payment already released this order's stock reservation (see
    # payments.pipeline.on_payment_failed), so it has to be re-reserved
    # here — unlike a plain abandoned-checkout retry (still AWAITING_PAYMENT,
    # nothing ever released its reservation), where re-reserving would
    # double-count it.
    if order.status == Order.Status.PAYMENT_FAILED:
        try:
            reserve_stock(order)
        except InsufficientStockError as exc:
            messages.error(request, str(exc))
            return redirect("cart:order_confirmation", order_number=order.order_number)
        order.status = Order.Status.AWAITING_PAYMENT
        order.save(update_fields=["status", "updated_at"])

    try:
        PaymentService().create_payment_for_order(order, request.user)
    except Exception:
        logger.exception("Failed to create a retry Razorpay order for order %s.", order.order_number)
        messages.error(request, "We couldn't start the payment right now. Please try again in a moment.")
    return redirect("cart:checkout_payment", order_number=order.order_number)


@login_required
def payment_success(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    if order.status == Order.Status.PAYMENT_CONFIRMED:
        # `purchase_tracked=1` marks this as the one-shot moment checkout
        # actually completed, so the order confirmation page fires GA4's
        # `purchase` event now but not on later revisits (e.g. from the
        # profile page's order history, which links here without this flag).
        confirmation_url = reverse("cart:order_confirmation", args=[order.order_number])
        return redirect(f"{confirmation_url}?purchase_tracked=1")
    # The client-side verification call that redirected here runs the same
    # pipeline synchronously, so the order should already be confirmed by
    # now — this only shows if that somehow hasn't landed yet (a webhook
    # will still confirm it shortly).
    return render(request, "cart/payment_processing.html", {"order": order})


@login_required
def payment_failed(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    return render(request, "cart/payment_failed.html", {"order": order})


@login_required
def order_confirmation(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    context = {
        "order": order,
        "return_requests": order.return_requests.all(),
        "can_request_return": is_order_returnable(order),
        "return_carrier_choices": Shipment.Carrier.choices,
        "fire_purchase_event": request.GET.get("purchase_tracked") == "1",
    }
    return render(request, "cart/order_confirmation.html", context)


@login_required
@require_POST
def cancel_order(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    try:
        cancel_order_service(order, cancelled_by=request.user, reason="Cancelled by buyer")
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Order {order.order_number} has been cancelled.")
    return redirect("cart:order_confirmation", order_number=order.order_number)


@login_required
def wishlist_detail(request):
    wishlist = _get_or_create_wishlist(request.user)
    context = {"wishlist_items": wishlist.items.select_related("product").prefetch_related("product__variants")}
    return render(request, "cart/wishlist.html", context)


@login_required
@require_POST
def toggle_wishlist(request, product_id):
    product = get_object_or_404(Product, pk=product_id, is_active=True)
    wishlist = _get_or_create_wishlist(request.user)

    item, created = WishlistItem.objects.get_or_create(wishlist=wishlist, product=product)
    if not created:
        item.delete()
        messages.info(request, f'"{product.title}" was removed from your wishlist.')
    else:
        messages.success(request, f'"{product.title}" was added to your wishlist.')

    return redirect(request.META.get("HTTP_REFERER") or "cart:wishlist_detail")
