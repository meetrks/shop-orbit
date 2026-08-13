"""Views for registration, login, logout, profile, and the store dashboard."""

import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from cart.models import Order, OrderItem
from cart.services import accept_order, cancel_order, mark_order_packed
from catalog.admin import store_stats
from catalog.models import Product
from catalog.profitability import product_profitability_report
from catalog.utils import paginate_products
from common.emails import send_templated_email
from payments.forms import RefundForm
from payments.models import Payment
from payments.services import PaymentService
from picweight.models import SupplierImageJob

from .forms import AddressForm, EmailLoginForm, ProfileForm, RegistrationForm
from .models import Address
from .services import create_address, delete_address, set_default_address, update_address

REVENUE_TREND_DAYS = 30
ORDERS_PER_PAGE = 25
DASHBOARD_PRODUCTS_PER_PAGE = 25
MY_ORDERS_PER_PAGE = 10


class EmailLoginView(LoginView):
    """Email/password login screen."""

    template_name = "accounts/login.html"
    authentication_form = EmailLoginForm
    redirect_authenticated_user = True


def register(request):
    if request.user.is_authenticated:
        return redirect("accounts:profile")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            auth_login(request, user)
            send_templated_email("welcome", {"user": user}, to=user.email)
            messages.success(request, f"Welcome to {settings.SITE_NAME}! Your account is ready.")
            return redirect("accounts:profile")
    else:
        form = RegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


def logout_view(request):
    auth_logout(request)
    messages.info(request, _("You have been logged out."))
    return redirect("pages:home")


@login_required
def profile(request):
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, _("Your profile has been updated."))
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=request.user)

    return render(request, "accounts/profile.html", {"form": form})


@login_required
def order_list(request):
    """Full, paginated order history — the profile page only ever showed the last 10."""
    orders = request.user.orders.all()
    paginator = Paginator(orders, MY_ORDERS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "accounts/orders.html", {"page_obj": page_obj})


@login_required
def address_list(request):
    return render(request, "accounts/addresses.html", {"addresses": request.user.addresses.all()})


@login_required
def address_add(request):
    if request.method == "POST":
        form = AddressForm(request.POST)
        if form.is_valid():
            is_default = form.cleaned_data["is_default"]
            create_address(request.user, form.save(commit=False), is_default=is_default)
            messages.success(request, _("Address added."))
            return redirect("accounts:addresses")
    else:
        form = AddressForm()

    return render(request, "accounts/address_form.html", {"form": form, "is_new": True})


@login_required
def address_edit(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)

    if request.method == "POST":
        form = AddressForm(request.POST, instance=address)
        if form.is_valid():
            is_default = form.cleaned_data["is_default"]
            update_address(form.save(commit=False), is_default=is_default)
            messages.success(request, _("Address updated."))
            return redirect("accounts:addresses")
    else:
        form = AddressForm(instance=address)

    return render(request, "accounts/address_form.html", {"form": form, "is_new": False, "address": address})


@login_required
@require_POST
def address_delete(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    delete_address(address)
    messages.info(request, _("Address removed."))
    return redirect("accounts:addresses")


@login_required
@require_POST
def address_set_default(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    set_default_address(address)
    messages.success(request, _("Default address updated."))
    return redirect("accounts:addresses")


def _revenue_by_day(days=REVENUE_TREND_DAYS):
    """
    Daily confirmed-order revenue for the last `days` days, zero-filled
    for days with no orders — a plain "only days with activity" series
    would misleadingly compress the x-axis on a quiet day, e.g. a single
    order 20 days ago right next to one from yesterday.
    """
    start_date = timezone.localdate() - datetime.timedelta(days=days - 1)

    totals_by_day = dict(
        OrderItem.objects.filter(order__status__in=Order.PAID_STATUSES, created_at__date__gte=start_date)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Sum(F("unit_price") * F("quantity")))
        .values_list("day", "total")
    )

    labels, totals = [], []
    for offset in range(days):
        day = start_date + datetime.timedelta(days=offset)
        labels.append(day.strftime("%d %b"))
        totals.append(float(totals_by_day.get(day, 0)))
    return labels, totals


def _order_status_breakdown():
    """One count per Order.Status value that actually has at least one order, all-time."""
    status_display = dict(Order.Status.choices)
    rows = Order.objects.values("status").annotate(count=Count("id")).order_by("-count")
    labels = [status_display.get(row["status"], row["status"]) for row in rows]
    counts = [row["count"] for row in rows]
    return labels, counts


@staff_member_required(login_url="accounts:login")
def store_dashboard(request):
    """
    The store-wide dashboard overview: stat cards, charts, and a short
    recent-activity preview. Linked from the Django admin's "Tools" panel
    (see templates/admin/index.html) — this is a regular Tailwind-themed
    page rather than a plain admin page, since tables-with-charts don't
    fit Django admin's own CSS well.

    The full product table and order list each have their own tab now
    (store_dashboard_products/store_dashboard_orders below) — this page
    just previews and links to them, rather than embedding a giant
    unpaginated table on top of three charts.

    Used to be scoped to `request.user`'s own listings (the removed
    seller dashboard); with a single operator, everything here is
    store-wide instead.
    """
    recent_order_items = OrderItem.objects.select_related("order", "product").order_by("-created_at")[:8]
    recent_picweight_jobs = SupplierImageJob.objects.order_by("-created_at")[:8]

    revenue_labels, revenue_totals = _revenue_by_day()
    status_labels, status_counts = _order_status_breakdown()

    context = {
        "recent_order_items": recent_order_items,
        "recent_picweight_jobs": recent_picweight_jobs,
        "revenue_chart_labels": revenue_labels,
        "revenue_chart_totals": revenue_totals,
        "status_chart_labels": status_labels,
        "status_chart_counts": status_counts,
        **store_stats(),
    }
    return render(request, "accounts/store_dashboard.html", context)


@staff_member_required(login_url="accounts:login")
def store_dashboard_products(request):
    """The Products tab: every product (+ its variants), paginated."""
    products = (
        Product.objects.all()
        .select_related("subcategory__category__department")
        .prefetch_related("variants")
        .order_by("-created_at")
    )
    page_obj = paginate_products(request, products, per_page=DASHBOARD_PRODUCTS_PER_PAGE)
    return render(request, "accounts/store_dashboard_products.html", {"page_obj": page_obj})


@staff_member_required(login_url="accounts:login")
def store_dashboard_orders(request):
    """
    The Orders tab: every order, optionally filtered to one status via
    `?status=`, paginated. Each row's available action (Mark Pending /
    Accept / Cancel / Mark Packed) is derived from `order.status` — see
    templates/accounts/store_dashboard_orders.html — but the real
    authorization/validity check happens server-side in the action views
    below (and again in cart.services), not just by which button the
    template happens to render.
    """
    orders = Order.objects.select_related("user").order_by("-created_at")

    status_filter = request.GET.get("status", "")
    if status_filter and status_filter in Order.Status.values:
        orders = orders.filter(status=status_filter)

    paginator = Paginator(orders, ORDERS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "status_filter": status_filter,
        "status_choices": Order.Status.choices,
        "preserved_querystring": f"status={status_filter}" if status_filter else "",
    }
    return render(request, "accounts/store_dashboard_orders.html", context)


@staff_member_required(login_url="accounts:login")
def store_dashboard_order_detail(request, order_number):
    """Full detail for one order: items, shipping, payments/refunds, shipment tracking, and status history."""
    order = get_object_or_404(
        Order.objects.select_related("user").prefetch_related("items__product", "items__variant", "payments__refunds"),
        order_number=order_number,
    )
    context = {
        "order": order,
        "shipment": getattr(order, "shipment", None),
        "status_history": order.status_history.select_related("changed_by").order_by("-created_at"),
    }
    return render(request, "accounts/store_dashboard_order_detail.html", context)


def _safe_redirect_back(request, order):
    """
    Redirects to wherever the action form said to come back to (the
    orders list or the order detail page, both of which set `next` to
    their own current URL), falling back to the order detail page.
    Validated via Django's own open-redirect guard rather than trusting
    the POSTed value outright — `next` is client-supplied.
    """
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("accounts:store_dashboard_order_detail", order_number=order.order_number)


@staff_member_required(login_url="accounts:login")
@require_POST
def store_dashboard_order_accept(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    try:
        accept_order(order, actor=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Order {order.order_number} accepted.")
    return _safe_redirect_back(request, order)


@staff_member_required(login_url="accounts:login")
@require_POST
def store_dashboard_order_mark_packed(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    try:
        mark_order_packed(order, actor=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Order {order.order_number} marked as packed.")
    return _safe_redirect_back(request, order)


@staff_member_required(login_url="accounts:login")
@require_POST
def store_dashboard_order_cancel(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "A cancellation reason is required.")
        return _safe_redirect_back(request, order)

    try:
        cancel_order(order, cancelled_by=request.user, reason=reason)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Order {order.order_number} has been cancelled.")
    return _safe_redirect_back(request, order)


@staff_member_required(login_url="accounts:login")
@require_POST
def store_dashboard_refund_payment(request, payment_id):
    """
    Initiates a full or partial refund against a captured payment,
    directly from the order detail page — same underlying
    `PaymentService.initiate_refund` call the Payments admin's "Initiate
    refund" action uses, so staff don't have to leave the dashboard for
    the common case. The admin action still exists too, unchanged.
    """
    payment = get_object_or_404(Payment, pk=payment_id)
    if not request.user.has_perm("payments.change_payment"):
        messages.error(request, "You don't have permission to initiate refunds.")
        return _safe_redirect_back(request, payment.order)

    remaining = payment.remaining_refundable
    form = RefundForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid refund amount.")
        return _safe_redirect_back(request, payment.order)

    amount = form.cleaned_data["amount"]
    reason = form.cleaned_data["reason"]
    if amount > remaining:
        messages.error(request, f"Cannot refund more than the remaining Rs. {remaining}.")
        return _safe_redirect_back(request, payment.order)

    try:
        PaymentService().initiate_refund(payment, amount, reason=reason, initiated_by=request.user)
    except Exception as exc:  # noqa: BLE001 — surfaced to the staff user, not swallowed
        messages.error(request, f"Refund failed: {exc}")
    else:
        messages.success(request, f"Refund of Rs. {amount} initiated for payment {payment.pk}.")
    return _safe_redirect_back(request, payment.order)


@staff_member_required(login_url="accounts:login")
def profitability_report(request):
    """
    Per-product gross margin and net contribution, estimated from
    OrderItem history plus the store-wide cost settings in
    config/settings/base.py (payment gateway fee %, shipping/packaging
    cost) — see catalog/profitability.py for the full calculation.
    Optionally scoped to a date range via `?from=YYYY-MM-DD&to=YYYY-MM-DD`.
    """
    date_from = _parse_date(request.GET.get("from"))
    date_to = _parse_date(request.GET.get("to"))

    rows = product_profitability_report(date_from=date_from, date_to=date_to)

    context = {
        "rows": rows,
        "date_from": date_from,
        "date_to": date_to,
        "total_revenue": sum((row["revenue"] for row in rows), Decimal("0.00")),
        "total_net_contribution": sum((row["net_contribution"] for row in rows), Decimal("0.00")),
        "any_cost_price_missing": any(row["cost_price_missing"] for row in rows),
    }
    return render(request, "accounts/profitability_report.html", context)


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None
