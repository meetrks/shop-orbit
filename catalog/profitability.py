"""
Per-product profitability: revenue actually received, cost of goods sold,
and estimated variable costs (payment gateway, shipping, packaging,
refunds), rolled up from OrderItem history. Powers the Store Dashboard's
profitability report — see accounts/views.py::profitability_report.

"Revenue actually received" is defined via `payments.Payment.status`, not
`Order.status` — an order can be CANCELLED after payment was captured
(awaiting a manual refund, see cart/services.py), and that revenue is real
until it's actually refunded, at which point the refund is deducted as a
cost below rather than the order being excluded outright.
"""

from collections import defaultdict
from decimal import Decimal

from django.conf import settings

from cart.models import Order, OrderItem
from payments.models import Payment, Refund

_REVENUE_RECOGNIZED_PAYMENT_STATUSES = [
    Payment.Status.CAPTURED,
    Payment.Status.PARTIALLY_REFUNDED,
    Payment.Status.REFUNDED,
]


def _paid_orders_queryset(date_from, date_to):
    orders = Order.objects.filter(payments__status__in=_REVENUE_RECOGNIZED_PAYMENT_STATUSES).distinct()
    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)
    return orders


def _refunded_amount_by_order_id(order_ids):
    """Sum of processed refund amounts per order, keyed by order id."""
    totals = defaultdict(Decimal)
    refunds = Refund.objects.filter(payment__order_id__in=order_ids, status=Refund.Status.PROCESSED).select_related(
        "payment"
    )
    for refund in refunds:
        totals[refund.payment.order_id] += refund.amount
    return totals


def product_profitability_report(date_from=None, date_to=None):
    """
    Returns a list of per-product profitability rows (dicts), sorted by
    `net_contribution` descending. Only includes products with at least
    one paid order item in the given date range (inclusive; either bound
    may be None for "no limit").
    """
    orders = _paid_orders_queryset(date_from, date_to)
    order_ids = list(orders.values_list("id", flat=True))
    if not order_ids:
        return []

    order_subtotals = dict(orders.values_list("id", "subtotal_amount"))
    # Gateway fees apply to what Razorpay actually captured — the
    # post-discount total, not the pre-discount subtotal used for
    # proportional allocation below.
    order_totals = {order.id: order.total_amount for order in orders.only("id", "subtotal_amount", "discount_amount")}
    refunded_by_order = _refunded_amount_by_order_id(order_ids)

    items = OrderItem.objects.filter(order_id__in=order_ids).select_related("product")

    rows_by_product = {}
    for item in items:
        product = item.product
        # A deleted product still has a frozen product_title/product_sku on
        # the OrderItem (see cart.models.OrderItem), so its historical sales
        # are still reportable — just without a live Product to key cost
        # tracking off of. Keyed by product_sku instead in that case.
        key = product.pk if product else f"deleted:{item.product_sku}"

        if key not in rows_by_product:
            rows_by_product[key] = {
                "product": product,
                "product_title": item.product_title,
                "product_sku": item.product_sku,
                "cost_price": product.cost_price if product else Decimal("0.00"),
                "cost_price_missing": (product.cost_price if product else Decimal("0.00")) == Decimal("0.00"),
                "units_sold": 0,
                "revenue": Decimal("0.00"),
                "cogs": Decimal("0.00"),
                "gateway_cost": Decimal("0.00"),
                "shipping_cost": Decimal("0.00"),
                "packaging_cost": Decimal("0.00"),
                "refund_cost": Decimal("0.00"),
            }
        row = rows_by_product[key]

        order_subtotal = order_subtotals.get(item.order_id) or Decimal("0.00")
        item_revenue = item.taxable_value  # net of GST — GST collected isn't the store's revenue
        item_share_of_order = (item.line_total / order_subtotal) if order_subtotal else Decimal("0.00")

        row["units_sold"] += item.quantity
        row["revenue"] += item_revenue
        row["cogs"] += row["cost_price"] * item.quantity
        order_total = order_totals.get(item.order_id) or Decimal("0.00")
        row["gateway_cost"] += (
            order_total * (Decimal(str(settings.PAYMENT_GATEWAY_FEE_PERCENT)) / 100) * item_share_of_order
        )
        row["shipping_cost"] += settings.DEFAULT_SHIPPING_COST_PER_ORDER * item_share_of_order
        row["packaging_cost"] += settings.DEFAULT_PACKAGING_COST_PER_UNIT * item.quantity
        row["refund_cost"] += refunded_by_order.get(item.order_id, Decimal("0.00")) * item_share_of_order

    rows = []
    for row in rows_by_product.values():
        gross_margin = row["revenue"] - row["cogs"]
        net_contribution = (
            gross_margin - row["gateway_cost"] - row["shipping_cost"] - row["packaging_cost"] - row["refund_cost"]
        )
        row["gross_margin"] = gross_margin.quantize(Decimal("0.01"))
        row["gross_margin_percent"] = (
            (gross_margin / row["revenue"] * 100).quantize(Decimal("0.1")) if row["revenue"] else Decimal("0.0")
        )
        row["net_contribution"] = net_contribution.quantize(Decimal("0.01"))
        for key in ("revenue", "cogs", "gateway_cost", "shipping_cost", "packaging_cost", "refund_cost"):
            row[key] = row[key].quantize(Decimal("0.01"))
        rows.append(row)

    rows.sort(key=lambda r: r["net_contribution"], reverse=True)
    return rows
