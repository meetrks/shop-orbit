"""
Keeps `Order.status` in sync with `Shipment.status` so the buyer-facing
order status (and the status-change email already wired up in
`cart.signals`) advances automatically once staff update a shipment,
instead of requiring a second manual edit on the Order itself.

Only forward fulfillment progress is synced (shipped/delivered) — a
RETURNED or CANCELLED shipment doesn't automatically flip the order's
status, since that decision (refund? reship? just note it?) is a staff
judgment call this signal shouldn't make for them.

Also writes an append-only `ShipmentStatusHistory` row on every status
change, mirroring `cart.signals`' `Order`/`OrderStatusHistory` pair
exactly — same `_status_change_actor`/`_status_change_reason` attribute
convention, so a caller (e.g. an admin action) can attribute a change
without either signal needing special-casing.
"""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from cart.models import Order

from .emails import send_shipment_ndr_email
from .models import Shipment, ShipmentStatusHistory

_ORDER_STATUS_BY_SHIPMENT_STATUS = {
    Shipment.Status.PICKED_UP: Order.Status.SHIPPED,
    Shipment.Status.IN_TRANSIT: Order.Status.SHIPPED,
    Shipment.Status.OUT_FOR_DELIVERY: Order.Status.SHIPPED,
    Shipment.Status.DELIVERED: Order.Status.DELIVERED,
}


@receiver(post_save, sender=Shipment)
def _sync_order_status(sender, instance, **kwargs):
    new_order_status = _ORDER_STATUS_BY_SHIPMENT_STATUS.get(instance.status)
    if not new_order_status:
        return
    order = instance.order
    if order.status != new_order_status:
        order.status = new_order_status
        order.save(update_fields=["status", "updated_at"])


@receiver(pre_save, sender=Shipment)
def _cache_previous_status(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_status = None
        return
    try:
        instance._previous_status = Shipment.objects.get(pk=instance.pk).status
    except Shipment.DoesNotExist:
        instance._previous_status = None


@receiver(post_save, sender=Shipment)
def _record_status_history(sender, instance, created, **kwargs):
    if created:
        return
    previous_status = getattr(instance, "_previous_status", None)
    if previous_status and previous_status != instance.status:
        ShipmentStatusHistory.objects.create(
            shipment=instance,
            previous_status=previous_status,
            new_status=instance.status,
            changed_by=getattr(instance, "_status_change_actor", None),
            reason=getattr(instance, "_status_change_reason", ""),
        )


@receiver(post_save, sender=Shipment)
def _notify_buyer_on_ndr(sender, instance, created, **kwargs):
    if created:
        return
    previous_status = getattr(instance, "_previous_status", None)
    if previous_status != instance.status and instance.status == Shipment.Status.NDR:
        send_shipment_ndr_email(instance)
