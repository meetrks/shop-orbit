"""Return-request notification emails, sent via common.emails."""

from django.conf import settings

from common.emails import send_templated_email


def send_return_requested_email(return_request):
    """Confirms the request to the buyer and alerts staff a review is needed."""
    send_templated_email(
        "return_requested",
        {"return_request": return_request, "order": return_request.order},
        to=return_request.user.email,
    )
    send_templated_email(
        "return_requested_staff",
        {"return_request": return_request, "order": return_request.order},
        to=settings.STAFF_NOTIFICATION_EMAIL,
    )


def send_return_approved_email(return_request):
    send_templated_email(
        "return_approved",
        {"return_request": return_request, "order": return_request.order},
        to=return_request.user.email,
    )


def send_return_rejected_email(return_request):
    send_templated_email(
        "return_rejected",
        {"return_request": return_request, "order": return_request.order},
        to=return_request.user.email,
    )
