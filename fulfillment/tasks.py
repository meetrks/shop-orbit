"""Periodic background sync of Delhivery tracking status for active shipments."""

import logging

from celery import shared_task
from django.conf import settings

from .couriers.delhivery import DelhiveryAPIError
from .models import Shipment
from .services import sync_shipment_tracking

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = [
    Shipment.Status.PENDING,
    Shipment.Status.PICKED_UP,
    Shipment.Status.IN_TRANSIT,
    Shipment.Status.OUT_FOR_DELIVERY,
    Shipment.Status.NDR,
]


@shared_task
def sync_all_shipment_tracking():
    """
    Polls Delhivery for every shipment that isn't in a terminal state yet.
    A no-op when DELHIVERY_API_TOKEN isn't configured, same as everything
    else Delhivery-related — see config/settings/base.py.

    One shipment's tracking API failure is logged (and reported to Sentry,
    once configured, via the WARNING-level LoggingIntegration) and skipped
    rather than aborting the whole batch.
    """
    if not settings.DELHIVERY_API_TOKEN:
        return

    shipments = Shipment.objects.filter(carrier=Shipment.Carrier.DELHIVERY, status__in=_ACTIVE_STATUSES).exclude(
        tracking_number=""
    )

    for shipment in shipments:
        try:
            sync_shipment_tracking(shipment)
        except DelhiveryAPIError:
            logger.warning("Failed to sync Delhivery tracking for shipment %s.", shipment.pk, exc_info=True)
