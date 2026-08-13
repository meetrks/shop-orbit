#!/usr/bin/env bash
# Zero-downtime deploy: checks out a fresh release, builds it, atomically
# switches the `current` symlink, restarts services, health-checks the
# result, and automatically rolls back on any failure.
#
# Run this ON THE SERVER (as the deploy user, or via sudo -u deploy) —
# .github/workflows/deploy.yml just SSHes in and calls
# $APP_DIR/scripts/deploy.sh; it doesn't reimplement any of this logic
# itself.
#
# $APP_DIR/scripts/ (NOT the release tree's own scripts/) is the stable
# entry point deploy.yml calls — it's a persistent copy outside the
# releases/current symlink dance, seeded once by install_server.sh and
# refreshed from each new release at the end of a successful deploy below,
# so "deploy" always has somewhere to run from even before a first
# release exists.
#
# Env vars (all optional, sensible defaults shown):
#   APP_DIR=/srv/shoporbit
#   GIT_REPO_URL                   Required — where to clone/fetch from.
#   GIT_REF=main                   Branch/tag/commit to deploy.
#   KEEP_RELEASES=5                Old releases to retain on disk (for rollback / disk hygiene).

set -euo pipefail

APP_DIR="${APP_DIR:-/srv/shoporbit}"
GIT_REF="${GIT_REF:-main}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
SHARED_DIR="$APP_DIR/shared"
RELEASES_DIR="$APP_DIR/releases"
CURRENT_LINK="$APP_DIR/current"
VENV_DIR="$SHARED_DIR/venv"

if [[ -z "${GIT_REPO_URL:-}" ]]; then
    echo "GIT_REPO_URL must be set (e.g. export GIT_REPO_URL=git@github.com:you/shoporbit.git)." >&2
    exit 1
fi

TIMESTAMP="$(date +%Y%m%d%H%M%S)"
RELEASE_DIR="$RELEASES_DIR/$TIMESTAMP"
PREVIOUS_RELEASE="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"

echo "==> Cloning $GIT_REPO_URL@$GIT_REF into $RELEASE_DIR"
git clone --depth 1 --branch "$GIT_REF" "$GIT_REPO_URL" "$RELEASE_DIR"

echo "==> Linking shared secrets/media into the new release"
ln -sfn "$SHARED_DIR/.env" "$RELEASE_DIR/.env"
rm -rf "$RELEASE_DIR/media"
ln -sfn "$SHARED_DIR/media" "$RELEASE_DIR/media"

echo "==> Syncing Python dependencies into the shared venv"
UV_PROJECT_ENVIRONMENT="$VENV_DIR" uv sync --project "$RELEASE_DIR" --frozen --no-dev

echo "==> Building frontend assets (npm run build, per docs/infrastructure.md)"
(cd "$RELEASE_DIR" && npm ci && npm run build)

echo "==> Running database migrations"
(cd "$RELEASE_DIR" && DJANGO_SETTINGS_MODULE=config.settings.production "$VENV_DIR/bin/python" manage.py migrate --noinput)

echo "==> Collecting static files"
(cd "$RELEASE_DIR" && DJANGO_SETTINGS_MODULE=config.settings.production "$VENV_DIR/bin/python" manage.py collectstatic --noinput)

echo "==> Switching current -> $RELEASE_DIR"
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"

echo "==> Restarting services"
systemctl restart gunicorn celery-worker celery-beat

echo "==> Health-checking the new release"
if ! bash "$RELEASE_DIR/scripts/health_check.sh"; then
    echo "!! Health check failed — rolling back to $PREVIOUS_RELEASE"
    if [[ -n "$PREVIOUS_RELEASE" ]]; then
        PREVIOUS_RELEASE="$PREVIOUS_RELEASE" bash "$RELEASE_DIR/scripts/rollback.sh"
    fi
    exit 1
fi

echo "==> Refreshing the persistent $APP_DIR/scripts/ entry point"
rsync -a --delete "$RELEASE_DIR/scripts/" "$APP_DIR/scripts/"

echo "==> Pruning old releases (keeping the last $KEEP_RELEASES)"
# shellcheck disable=SC2012
ls -1dt "$RELEASES_DIR"/*/ 2>/dev/null | tail -n "+$((KEEP_RELEASES + 1))" | xargs -r rm -rf

echo "==> Deploy complete: $RELEASE_DIR is now live."
