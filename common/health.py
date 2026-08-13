"""
Unauthenticated health-check endpoints for load balancers, deploy scripts,
and uptime monitors — see config/urls.py for the /health/live/ and
/health/ready/ routes, and scripts/health_check.sh (which also separately
pings Celery workers; that's not checked here, see this module's
docstring notes below).

Liveness vs. readiness follows the standard Kubernetes-style distinction
even though this project doesn't run on Kubernetes: liveness answers "is
the process up at all" (used to decide whether to restart it), readiness
answers "can it actually serve a request right now" (used to decide
whether to send it traffic / whether a deploy succeeded).
"""

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse


def liveness_view(request):
    """
    The process is up and Django can handle a request — no dependency
    checks. A load balancer/systemd restart policy uses this to decide
    "is this worker process even alive", not "is the app fully healthy".
    """
    return JsonResponse({"status": "ok"})


def readiness_view(request):
    """
    Can this instance actually serve real traffic right now — checks the
    two dependencies every request potentially touches (database, cache).
    Celery isn't checked here: pinging a worker over HTTP would add
    request latency for a check that only matters to deploy scripts and
    monitoring, not to whether a page can load — see
    scripts/health_check.sh, which pings Celery separately via
    `celery -A config inspect ping`.
    """
    checks = {}
    healthy = True

    try:
        connection.ensure_connection()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
        healthy = False

    try:
        cache.set("healthcheck", "ok", timeout=5)
        checks["cache"] = "ok" if cache.get("healthcheck") == "ok" else "error: round-trip mismatch"
        if checks["cache"] != "ok":
            healthy = False
    except Exception as exc:
        checks["cache"] = f"error: {exc}"
        healthy = False

    return JsonResponse({"status": "ok" if healthy else "error", **checks}, status=200 if healthy else 503)
