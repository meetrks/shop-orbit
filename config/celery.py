"""
Celery application for shoporbit. Background work (currently:
outbound email, see common/tasks.py) runs through this app rather than
inline in the request/response cycle.

Run a worker locally with:
    uv run celery -A config worker -l info

In production, the systemd worker unit (see deploy/systemd/celery-worker.service)
consumes both queues: `celery -A config worker -Q default,emails -l info`.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("shoporbit")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Two queues, matching the only two genuinely different task types that
# exist today: outbound email (common.tasks.send_email_task) and
# everything else. Not pre-building unused "heavy"/"reports" queues with
# nothing to route to them — split further if/when a real need shows up.
app.conf.task_routes = {
    "common.tasks.send_email_task": {"queue": "emails"},
}
app.conf.task_default_queue = "default"
