"""Shipment notification emails, sent via common.emails."""

from common.emails import send_templated_email


def send_shipment_ndr_email(shipment):
    """Notifies the buyer a delivery attempt failed and another will typically follow automatically."""
    send_templated_email(
        "shipment_ndr",
        {"shipment": shipment, "order": shipment.order},
        to=shipment.order.user.email,
    )
