"""
Celery task for the final send step of an outbound email. Kept separate
from `common.emails` so the template-rendering step (which can reference
Decimal amounts, model instances, etc.) never has to cross the task
queue's serialization boundary — only the already-rendered subject/body
strings do.

`common` isn't a registered Django app (see common/models.py's docstring),
so it's outside Celery's `autodiscover_tasks()` sweep. That's fine:
`common.emails` imports this module directly, and `@shared_task` binds to
the current Celery app lazily on first use regardless of when/how it's
imported.
"""

import logging

from celery import shared_task
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(self, *, subject, text_body, html_body, from_email, to, cc, reply_to=None):
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=to,
        cc=cc,
        reply_to=[reply_to] if reply_to else None,
    )
    if html_body:
        message.attach_alternative(html_body, "text/html")

    try:
        message.send(fail_silently=False)
    except Exception as exc:
        logger.exception(
            "Failed to send email '%s' to %s (attempt %s/%s).",
            subject,
            to,
            self.request.retries + 1,
            self.max_retries + 1,
        )
        raise self.retry(exc=exc)
