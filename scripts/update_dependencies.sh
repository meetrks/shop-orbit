#!/usr/bin/env bash
# Upgrades locked dependency versions and re-syncs the venv. Deliberately
# NOT part of every deploy.sh run — dependency upgrades should be a
# reviewed, deliberate action (run locally, diff the lockfile, commit,
# then deploy normally), not something that silently happens on every
# push to main.
#
# Run locally (not on the server):
#   scripts/update_dependencies.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Current lockfile hash: $(shasum -a 256 uv.lock 2>/dev/null | awk '{print $1}' || echo 'none')"

echo "==> Upgrading locked dependency versions"
uv lock --upgrade

echo "==> Syncing the local venv to match"
uv sync --all-groups

echo "==> Lockfile diff:"
git diff --stat -- uv.lock pyproject.toml || true

cat <<'EOF'

==> Review the diff above, run the test suite (uv run python manage.py test),
    then commit uv.lock and pyproject.toml if everything looks right.
    A normal `scripts/deploy.sh` run will pick up the new lockfile on its
    next `uv sync` — this script only updates it, it doesn't deploy.
EOF
