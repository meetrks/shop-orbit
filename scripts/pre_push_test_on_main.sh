#!/usr/bin/env bash
# Pre-push git hook body: runs the full test suite, but only when the push
# target is main — see .pre-commit-config.yaml (pre-push stage) and
# CONTRIBUTING.md.
#
# Reads git's pre-push stdin protocol: one line per ref being pushed,
# "<local ref> <local sha1> <remote ref> <remote sha1>".
set -euo pipefail

while read -r local_ref local_sha remote_ref remote_sha; do
    if [ "$remote_ref" = "refs/heads/main" ]; then
        echo "Pushing to main — running the test suite first..."
        uv run python manage.py test
        exit $?
    fi
done

exit 0
