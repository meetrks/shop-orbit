"""
Stock reservation and movement service functions — the only code allowed
to change `Product`/`ProductVariant` `stock_count`/`reserved_count`, or
write a `StockMovement` row. Every function here is guarded by
`select_for_update` on the product/variant row so concurrent checkouts
racing for the same last unit can't both succeed.
"""

import logging
from decimal import Decimal

from django.db import transaction

from catalog.models import Product, ProductVariant

from .models import PurchaseOrder, PurchaseOrderLine, StockMovement

logger = logging.getLogger(__name__)


class InsufficientStockError(Exception):
    """Raised by `reserve_stock` when a line's available-to-sell can't cover the requested quantity."""


def reserve_stock(order):
    """
    Holds stock for every line in `order` against other buyers' checkouts
    for the duration of the Razorpay payment attempt. Raises
    `InsufficientStockError` (leaving nothing reserved, since the whole
    call is one transaction) if any single line can't be covered — the
    caller should treat the order as unreservable rather than partially
    reserve it.
    """
    with transaction.atomic():
        for item in order.items.select_related("product", "variant").all():
            if item.variant_id:
                variant = ProductVariant.objects.select_for_update().get(pk=item.variant_id)
                if variant.available_to_sell < item.quantity:
                    raise InsufficientStockError(f'Only {variant.available_to_sell} of "{variant}" left in stock.')
                variant.reserved_count += item.quantity
                variant.save(update_fields=["reserved_count", "updated_at"])
                StockMovement.objects.create(
                    product=variant.product,
                    variant=variant,
                    movement_type=StockMovement.MovementType.RESERVED,
                    reserved_delta=item.quantity,
                    order=order,
                )
            elif item.product_id:
                product = Product.objects.select_for_update().get(pk=item.product_id)
                if product.available_to_sell < item.quantity:
                    raise InsufficientStockError(f'Only {product.available_to_sell} of "{product}" left in stock.')
                product.reserved_count += item.quantity
                product.save(update_fields=["reserved_count", "updated_at"])
                StockMovement.objects.create(
                    product=product,
                    movement_type=StockMovement.MovementType.RESERVED,
                    reserved_delta=item.quantity,
                    order=order,
                )


def release_reservation(order):
    """
    Gives back a reservation without touching real `stock_count` — used
    when a payment fails, an unpaid order is cancelled, or the reservation
    expiry sweep releases an abandoned checkout. Clamps at the currently
    reserved amount so a double-release (e.g. a retried task) can't drive
    `reserved_count` negative.
    """
    with transaction.atomic():
        for item in order.items.select_related("product", "variant").all():
            if item.variant_id:
                variant = ProductVariant.objects.select_for_update().get(pk=item.variant_id)
                released = min(item.quantity, variant.reserved_count)
                variant.reserved_count -= released
                variant.save(update_fields=["reserved_count", "updated_at"])
                StockMovement.objects.create(
                    product=variant.product,
                    variant=variant,
                    movement_type=StockMovement.MovementType.RELEASED,
                    reserved_delta=-released,
                    order=order,
                )
            elif item.product_id:
                product = Product.objects.select_for_update().get(pk=item.product_id)
                released = min(item.quantity, product.reserved_count)
                product.reserved_count -= released
                product.save(update_fields=["reserved_count", "updated_at"])
                StockMovement.objects.create(
                    product=product,
                    movement_type=StockMovement.MovementType.RELEASED,
                    reserved_delta=-released,
                    order=order,
                )


def convert_reservation_to_sale(order):
    """
    Called once a payment is captured: the reserved units are now
    actually sold, so this decrements both `stock_count` and
    `reserved_count`. Floors `stock_count` at zero rather than raising if
    the item was somehow oversold in the gap between reservation and
    capture — the customer's payment has already been taken at this
    point, so refusing to confirm the order isn't an option; a staff
    member needs to sort out the backorder manually (the resulting
    `StockMovement` row is the paper trail for that).
    """
    with transaction.atomic():
        for item in order.items.select_related("product", "variant").all():
            if item.variant_id:
                variant = ProductVariant.objects.select_for_update().get(pk=item.variant_id)
                if variant.stock_count < item.quantity:
                    logger.error(
                        "Stock oversold for order %s: variant %s has %s left, order wants %s.",
                        order.order_number,
                        variant.sku,
                        variant.stock_count,
                        item.quantity,
                    )
                    stock_delta = -variant.stock_count
                    variant.stock_count = 0
                else:
                    stock_delta = -item.quantity
                    variant.stock_count -= item.quantity
                reserved_delta = -min(item.quantity, variant.reserved_count)
                variant.reserved_count += reserved_delta
                variant.save(update_fields=["stock_count", "reserved_count", "updated_at"])
                StockMovement.objects.create(
                    product=variant.product,
                    variant=variant,
                    movement_type=StockMovement.MovementType.SOLD,
                    stock_delta=stock_delta,
                    reserved_delta=reserved_delta,
                    order=order,
                )
            elif item.product_id:
                product = Product.objects.select_for_update().get(pk=item.product_id)
                if product.stock_count < item.quantity:
                    logger.error(
                        "Stock oversold for order %s: product %s has %s left, order wants %s.",
                        order.order_number,
                        product.sku,
                        product.stock_count,
                        item.quantity,
                    )
                    stock_delta = -product.stock_count
                    product.stock_count = 0
                else:
                    stock_delta = -item.quantity
                    product.stock_count -= item.quantity
                reserved_delta = -min(item.quantity, product.reserved_count)
                product.reserved_count += reserved_delta
                product.save(update_fields=["stock_count", "reserved_count", "updated_at"])
                StockMovement.objects.create(
                    product=product,
                    movement_type=StockMovement.MovementType.SOLD,
                    stock_delta=stock_delta,
                    reserved_delta=reserved_delta,
                    order=order,
                )


def restore_stock_for_cancellation(order):
    """Gives back real on-hand stock for an order that was cancelled after its payment had already captured."""
    with transaction.atomic():
        for item in order.items.select_related("product", "variant").all():
            if item.variant_id:
                variant = ProductVariant.objects.select_for_update().get(pk=item.variant_id)
                variant.stock_count += item.quantity
                variant.save(update_fields=["stock_count", "updated_at"])
                StockMovement.objects.create(
                    product=variant.product,
                    variant=variant,
                    movement_type=StockMovement.MovementType.RESTORED,
                    stock_delta=item.quantity,
                    order=order,
                )
            elif item.product_id:
                product = Product.objects.select_for_update().get(pk=item.product_id)
                product.stock_count += item.quantity
                product.save(update_fields=["stock_count", "updated_at"])
                StockMovement.objects.create(
                    product=product,
                    movement_type=StockMovement.MovementType.RESTORED,
                    stock_delta=item.quantity,
                    order=order,
                )


def record_restock(product, variant=None, quantity=1, *, note="", created_by=None, purchase_order=None):
    """
    Records new stock arriving (a supplier delivery, a stocktake finding
    more than expected) — always a positive quantity. For any other kind
    of manual correction (damage write-off, a negative stocktake
    adjustment), use `record_adjustment` instead.
    """
    if quantity <= 0:
        raise ValueError("record_restock quantity must be positive — use record_adjustment for corrections.")

    with transaction.atomic():
        if variant is not None:
            variant = ProductVariant.objects.select_for_update().get(pk=variant.pk)
            variant.stock_count += quantity
            variant.save(update_fields=["stock_count", "updated_at"])
            StockMovement.objects.create(
                product=variant.product,
                variant=variant,
                movement_type=StockMovement.MovementType.RESTOCKED,
                stock_delta=quantity,
                note=note,
                created_by=created_by,
                purchase_order=purchase_order,
            )
            return variant
        else:
            product = Product.objects.select_for_update().get(pk=product.pk)
            product.stock_count += quantity
            product.save(update_fields=["stock_count", "updated_at"])
            StockMovement.objects.create(
                product=product,
                movement_type=StockMovement.MovementType.RESTOCKED,
                stock_delta=quantity,
                note=note,
                created_by=created_by,
                purchase_order=purchase_order,
            )
            return product


def restock_from_return(product, variant=None, quantity=1, *, return_request, note="", created_by=None):
    """
    Records a returned unit going back into sellable stock — distinct
    from `record_restock` (a supplier delivery) so the ledger keeps them
    apart for reporting. Only called for lines staff have actually judged
    resellable; see `returns.services.mark_return_received`.
    """
    if quantity <= 0:
        raise ValueError("restock_from_return quantity must be positive.")

    with transaction.atomic():
        if variant is not None:
            variant = ProductVariant.objects.select_for_update().get(pk=variant.pk)
            variant.stock_count += quantity
            variant.save(update_fields=["stock_count", "updated_at"])
            StockMovement.objects.create(
                product=variant.product,
                variant=variant,
                movement_type=StockMovement.MovementType.RETURNED,
                stock_delta=quantity,
                note=note,
                created_by=created_by,
                return_request=return_request,
            )
            return variant
        else:
            product = Product.objects.select_for_update().get(pk=product.pk)
            product.stock_count += quantity
            product.save(update_fields=["stock_count", "updated_at"])
            StockMovement.objects.create(
                product=product,
                movement_type=StockMovement.MovementType.RETURNED,
                stock_delta=quantity,
                note=note,
                created_by=created_by,
                return_request=return_request,
            )
            return product


def record_adjustment(product, variant=None, delta=0, *, note="", created_by=None):
    """
    Manual stock correction outside the normal reserve/sell/restock flow —
    a stocktake discrepancy, damage write-off, etc. `delta` can be
    positive or negative; `stock_count` is floored at zero rather than
    allowed to go negative.
    """
    if delta == 0:
        raise ValueError("record_adjustment delta must be non-zero.")

    with transaction.atomic():
        target = variant if variant is not None else product
        model = type(target)
        obj = model.objects.select_for_update().get(pk=target.pk)
        new_stock_count = max(obj.stock_count + delta, 0)
        applied_delta = new_stock_count - obj.stock_count
        obj.stock_count = new_stock_count
        obj.save(update_fields=["stock_count", "updated_at"])
        StockMovement.objects.create(
            product=obj.product if variant is not None else obj,
            variant=obj if variant is not None else None,
            movement_type=StockMovement.MovementType.ADJUSTED,
            stock_delta=applied_delta,
            note=note,
            created_by=created_by,
        )
        return obj


# ---------------------------------------------------------------------------
# Purchase orders
# ---------------------------------------------------------------------------


def create_purchase_order(supplier, lines_data, *, created_by=None, expected_delivery_date=None, notes=""):
    """
    Creates a draft `PurchaseOrder`. `lines_data` is an iterable of dicts:
    `{"product": Product, "variant": ProductVariant | None,
    "quantity_ordered": int, "unit_cost": Decimal}`.
    """
    lines_data = list(lines_data)
    if not lines_data:
        raise ValueError("A purchase order needs at least one line.")

    with transaction.atomic():
        po = PurchaseOrder.objects.create(
            supplier=supplier,
            expected_delivery_date=expected_delivery_date,
            notes=notes,
            created_by=created_by,
        )
        for line in lines_data:
            PurchaseOrderLine.objects.create(
                purchase_order=po,
                product=line["product"],
                variant=line.get("variant"),
                quantity_ordered=line["quantity_ordered"],
                unit_cost=line.get("unit_cost", Decimal("0.00")),
            )
    return po


def mark_purchase_order_ordered(po):
    if po.status != PurchaseOrder.Status.DRAFT:
        raise ValueError(
            f"Purchase order {po.po_number} must be in Draft status to mark as ordered "
            f"(currently '{po.get_status_display()}')."
        )
    po.status = PurchaseOrder.Status.ORDERED
    po.save(update_fields=["status", "updated_at"])
    return po


def receive_purchase_order_line(line, quantity, *, received_by=None):
    """
    Records receipt of `quantity` units against one PO line (in full or in
    part), restocks the corresponding product/variant, and re-evaluates
    the parent purchase order's status (`RECEIVED` once every line is
    fully received, `PARTIALLY_RECEIVED` if only some are).
    """
    if quantity <= 0:
        raise ValueError("Received quantity must be positive.")
    if quantity > line.quantity_remaining:
        raise ValueError(f"Can't receive {quantity} units — only {line.quantity_remaining} remain on this line.")

    with transaction.atomic():
        record_restock(
            line.product,
            variant=line.variant,
            quantity=quantity,
            note=f"Received against {line.purchase_order.po_number}",
            created_by=received_by,
            purchase_order=line.purchase_order,
        )
        line.quantity_received += quantity
        line.save(update_fields=["quantity_received", "updated_at"])

        po = line.purchase_order
        if po.is_fully_received:
            po.status = PurchaseOrder.Status.RECEIVED
        elif po.is_partially_received:
            po.status = PurchaseOrder.Status.PARTIALLY_RECEIVED
        po.save(update_fields=["status", "updated_at"])
    return line


def create_purchase_order_from_low_stock(supplier, *, created_by=None):
    """
    Convenience: drafts a purchase order for every active product whose
    `default_supplier` is `supplier` and whose available-to-sell is at or
    below its `low_stock_threshold`. Suggests topping back up to twice the
    threshold — a simple, deliberately naive heuristic; adjust quantities
    on the draft before marking it ordered. Variant-level stock isn't
    considered here — `default_supplier` is a product-level field.
    """
    products = Product.objects.filter(default_supplier=supplier, is_active=True)
    lines_data = []
    for product in products:
        if product.available_to_sell <= product.low_stock_threshold:
            suggested_quantity = max(product.low_stock_threshold * 2 - product.available_to_sell, 1)
            lines_data.append(
                {
                    "product": product,
                    "variant": None,
                    "quantity_ordered": suggested_quantity,
                    "unit_cost": Decimal("0.00"),
                }
            )

    if not lines_data:
        raise ValueError(f'No low-stock products found with "{supplier}" as their default supplier.')

    return create_purchase_order(
        supplier, lines_data, created_by=created_by, notes="Auto-drafted from low-stock products."
    )
