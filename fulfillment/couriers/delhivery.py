"""
A thin client for Delhivery's B2C REST API — waybill (AWB) creation,
tracking, and shipping-label PDF download.

Written without a live Delhivery account to test against (this project
went straight from "we want direct courier integration" to code, ahead of
Delhivery API access actually being provisioned), following the request/
response shapes Delhivery's B2C API has publicly documented for years.
Treat this as a solid first draft, not a verified-working integration:
before going live, confirm against your Delhivery developer dashboard that
`DELHIVERY_BASE_URL`, these endpoint paths, and the payload/response field
names below still match — carrier APIs do change, and there's no
substitute for one real end-to-end test against a live (or staging)
account once credentials exist.
"""

import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Delhivery's tracking status strings, mapped to our own Shipment.Status.
# Deliberately conservative: an unrecognized status maps to None (see
# `map_tracking_status`) so a sync never guesses and silently corrupts our
# own status — better to log it and leave the shipment as it was.
_STATUS_MAP = {
    "manifested": "pending",
    "not picked": "pending",
    "picked up": "picked_up",
    "in transit": "in_transit",
    "pending": "in_transit",
    "dispatched": "out_for_delivery",
    "out for delivery": "out_for_delivery",
    "delivered": "delivered",
    "rto": "returned",
    "return": "returned",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "ndr": "ndr",
    "undelivered": "ndr",
}


class DelhiveryAPIError(Exception):
    """Raised on any non-successful response from Delhivery's API — a failed call, not a caught/expected state."""


def _client_configured():
    return bool(settings.DELHIVERY_API_TOKEN)


def _require_configured():
    if not _client_configured():
        raise DelhiveryAPIError(
            "DELHIVERY_API_TOKEN isn't set — Delhivery integration is configured but inactive. "
            "See config/settings/base.py and .env.example."
        )


def _headers():
    return {
        "Authorization": f"Token {settings.DELHIVERY_API_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def create_waybill(order):
    """
    Manifests `order` with Delhivery and returns the assigned waybill
    (AWB) number. Raises DelhiveryAPIError on any failure — including a
    "Success"-shaped response that Delhivery itself marked as a per-package
    failure (bad pincode, pickup location not recognized, etc.).
    """
    _require_configured()

    payload = {
        "shipments": [
            {
                "name": order.shipping_full_name,
                "add": f"{order.shipping_address_line1} {order.shipping_address_line2}".strip(),
                "city": order.shipping_city,
                "state": order.shipping_state,
                "country": order.shipping_country,
                "pin": order.shipping_postal_code,
                "phone": order.shipping_phone_number,
                "order": order.order_number,
                "payment_mode": "Prepaid",
                "total_amount": str(order.total_amount),
                "cod_amount": "0",
                "products_desc": ", ".join(item.product_title for item in order.items.all()[:5]),
                "quantity": str(sum(item.quantity for item in order.items.all())),
                "weight": str(settings.DELHIVERY_DEFAULT_PACKAGE_WEIGHT_GRAMS),
            }
        ],
        "pickup_location": {"name": settings.DELHIVERY_PICKUP_LOCATION_NAME},
    }

    # Delhivery's manifest endpoint takes form-encoded `format`/`data`
    # fields (the JSON payload as a string), not a raw JSON request body —
    # a known quirk of this particular endpoint, unlike the rest of their API.
    response = requests.post(
        f"{settings.DELHIVERY_BASE_URL}/api/cmu/create.json",
        headers={"Authorization": _headers()["Authorization"]},
        data={"format": "json", "data": json.dumps(payload)},
        timeout=15,
    )
    _raise_for_status(response, "creating a waybill for order %s", order.order_number)

    body = response.json()
    packages = body.get("packages") or []
    if not packages or not packages[0].get("waybill"):
        logger.error("Delhivery manifest for order %s returned no waybill: %s", order.order_number, body)
        raise DelhiveryAPIError(f"Delhivery didn't return a waybill for order {order.order_number}: {body}")

    return packages[0]["waybill"]


def track(waybill):
    """Fetches Delhivery's current tracking status for `waybill`. Returns the raw parsed JSON response."""
    _require_configured()

    response = requests.get(
        f"{settings.DELHIVERY_BASE_URL}/api/v1/packages/json/",
        headers=_headers(),
        params={"waybill": waybill},
        timeout=15,
    )
    _raise_for_status(response, "tracking waybill %s", waybill)
    return response.json()


def map_tracking_status(tracking_response):
    """
    Extracts Delhivery's current status string from `track()`'s response
    and maps it to one of our own `Shipment.Status` values. Returns None
    if the shape is unexpected or the status string isn't recognized —
    callers should leave the shipment's status untouched in that case
    rather than guess.
    """
    try:
        shipment_data = tracking_response["ShipmentData"][0]["Shipment"]
        raw_status = shipment_data["Status"]["Status"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Unexpected Delhivery tracking response shape: %s", tracking_response)
        return None

    mapped = _STATUS_MAP.get(raw_status.strip().lower())
    if mapped is None:
        logger.warning("Unrecognized Delhivery tracking status %r — leaving shipment status unchanged.", raw_status)
    return mapped


def fetch_label_pdf(waybill):
    """Downloads the shipping label PDF for `waybill` as raw bytes."""
    _require_configured()

    response = requests.get(
        f"{settings.DELHIVERY_BASE_URL}/api/p/packing_slip/",
        headers=_headers(),
        params={"wbns": waybill, "pdf": "true"},
        timeout=15,
    )
    _raise_for_status(response, "fetching the shipping label for waybill %s", waybill)
    return response.content


def _raise_for_status(response, action_fmt, *action_args):
    if response.ok:
        return
    action = action_fmt % action_args
    logger.error("Delhivery error while %s: %s %s", action, response.status_code, response.text)
    raise DelhiveryAPIError(f"Delhivery error while {action}: HTTP {response.status_code}")
