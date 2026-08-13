#!/usr/bin/env bash
# Checks the app is actually up: HTTP health endpoints plus a Celery
# worker ping. Exits non-zero on any failure — deploy.sh/rollback.sh use
# that exit code to decide whether a release is good.
#
# Celery is checked here (not over HTTP from common/health.py) since this
# script is exactly the place that cares about worker liveness, and a
# `celery inspect ping` round-trip is too slow/unreliable to run inline
# on every web request.
#
# Env vars:
#   APP_DIR=/srv/shoporbit
#   HEALTH_CHECK_URL=http://localhost   Reaches Gunicorn through Nginx by default;
#                                       override to hit the socket directly if Nginx isn't up yet.
#   SKIP_CELERY_CHECK=0                 Set to 1 to skip the Celery ping (e.g. right
#                                       after install, before celery-worker is running).

set -euo pipefail

APP_DIR="${APP_DIR:-/srv/shoporbit}"
VENV_DIR="$APP_DIR/shared/venv"
CURRENT_LINK="$APP_DIR/current"
HEALTH_CHECK_URL="${HEALTH_CHECK_URL:-http://localhost}"
SKIP_CELERY_CHECK="${SKIP_CELERY_CHECK:-0}"

FAILED=0

echo "==> Checking $HEALTH_CHECK_URL/health/live/"
if ! curl -fsS "$HEALTH_CHECK_URL/health/live/" >/dev/null; then
    echo "FAIL: liveness check failed" >&2
    FAILED=1
fi

echo "==> Checking $HEALTH_CHECK_URL/health/ready/"
if ! curl -fsS "$HEALTH_CHECK_URL/health/ready/" >/dev/null; then
    echo "FAIL: readiness check failed (database or cache unreachable)" >&2
    FAILED=1
fi

if [[ "$SKIP_CELERY_CHECK" != "1" ]]; then
    echo "==> Pinging Celery workers"
    if [[ -d "$CURRENT_LINK" && -x "$VENV_DIR/bin/celery" ]]; then
        if ! (cd "$CURRENT_LINK" && DJANGO_SETTINGS_MODULE=config.settings.production "$VENV_DIR/bin/celery" -A config inspect ping --timeout 5 >/dev/null 2>&1); then
            echo "FAIL: no Celery worker responded to ping" >&2
            FAILED=1
        fi
    else
        echo "SKIP: venv/current release not found (expected during initial install)"
    fi
fi

if [[ "$FAILED" -eq 0 ]]; then
    echo "==> All health checks passed."
else
    echo "==> One or more health checks FAILED." >&2
fi

exit "$FAILED"
