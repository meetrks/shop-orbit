"""Back-in-stock notification emails."""

from django.utils import timezone

from common.emails import send_templated_email

from .models import StockAlert


def notify_back_in_stock(product, variant=None):
    """Emails everyone with a pending alert for this product/variant, then marks them notified."""
    alerts = StockAlert.objects.filter(product=product, variant=variant, notified_at__isnull=True)
    for alert in alerts:
        send_templated_email("back_in_stock", {"product": product, "variant": variant}, to=alert.email)
    alerts.update(notified_at=timezone.now())
