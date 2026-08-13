#!/usr/bin/env bash
# Repoints `current` at the previous release and restarts services.
# Called automatically by deploy.sh on a failed health check; also safe
# to run manually if a deployed release turns out to be bad after the
# fact (e.g. a bug only shows up under real traffic).
#
# Env vars:
#   APP_DIR=/srv/shoporbit
#   PREVIOUS_RELEASE   Optional — the release directory to roll back to.
#                      If unset, auto-detects the second-most-recent
#                      release under $APP_DIR/releases (i.e. "current" is
#                      assumed bad, "the one before it" is the target).

set -euo pipefail

APP_DIR="${APP_DIR:-/srv/shoporbit}"
RELEASES_DIR="$APP_DIR/releases"
CURRENT_LINK="$APP_DIR/current"

if [[ -z "${PREVIOUS_RELEASE:-}" ]]; then
    CURRENT_TARGET="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
    # shellcheck disable=SC2012
    PREVIOUS_RELEASE="$(ls -1dt "$RELEASES_DIR"/*/ 2>/dev/null | grep -v -F "${CURRENT_TARGET:-__none__}/" | head -n 1 | sed 's:/$::')"
fi

if [[ -z "$PREVIOUS_RELEASE" || ! -d "$PREVIOUS_RELEASE" ]]; then
    echo "No previous release found to roll back to under $RELEASES_DIR." >&2
    exit 1
fi

echo "==> Rolling back: current -> $PREVIOUS_RELEASE"
ln -sfn "$PREVIOUS_RELEASE" "$CURRENT_LINK"

echo "==> Restarting services"
systemctl restart gunicorn celery-worker celery-beat

echo "==> Verifying rollback health"
bash "$PREVIOUS_RELEASE/scripts/health_check.sh"

echo "==> Rollback complete: $PREVIOUS_RELEASE is now live."
