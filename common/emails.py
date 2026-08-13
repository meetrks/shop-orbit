"""
Shared outbound-email helper used by every app that sends notifications.

Every email is rendered from a pair of templates — `{prefix}.subject.txt`,
`{prefix}.txt`, and optionally `{prefix}.html` — under `templates/emails/`,
so individual apps (cart, accounts, ...) never touch SMTP/message plumbing
directly; they just call `send_templated_email` with a template prefix and
a context dict.

Rendering happens synchronously here (so a broken template fails loudly,
in the request that triggered it, rather than surfacing later in a
background worker log nobody's watching); the actual send is dispatched to
`common.tasks.send_email_task` as a Celery task, with automatic retries.
That means a slow/unreachable SMTP server can no longer add latency to the
request that triggered the notification, and a transient failure gets
retried in the background instead of silently dropping the email.
"""

from django.conf import settings
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string

from .tasks import send_email_task


def send_templated_email(template_prefix, context, to, from_email=None, cc=None, reply_to=None):
    """
    Renders and queues one email for sending. `to` and `cc` may be a single
    address or a list. Returns True if the message was handed off to the
    task queue successfully (not whether it was actually delivered —
    delivery failures/retries happen in `common.tasks.send_email_task`).

    `reply_to` defaults to `settings.REPLY_TO_EMAIL` (the support inbox) —
    pass `reply_to=""` explicitly to omit the header for a specific email.
    """
    if isinstance(to, str):
        to = [to]
    if isinstance(cc, str):
        cc = [cc]
    to = [address for address in to if address]
    if not to:
        return False

    context = {"site_name": settings.SITE_NAME, "site_domain": settings.SITE_DOMAIN, **context}

    subject = render_to_string(f"emails/{template_prefix}.subject.txt", context).strip()
    text_body = render_to_string(f"emails/{template_prefix}.txt", context).strip()

    try:
        html_body = render_to_string(f"emails/{template_prefix}.html", context)
    except TemplateDoesNotExist:
        html_body = None

    resolved_reply_to = settings.REPLY_TO_EMAIL if reply_to is None else reply_to

    send_email_task.delay(
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        from_email=from_email or settings.DEFAULT_FROM_EMAIL,
        to=to,
        cc=[address for address in (cc or []) if address],
        reply_to=resolved_reply_to,
    )
    return True
