#!/usr/bin/env bash
# Non-interactive superuser creation/reset — safe to re-run (checks
# whether the user already exists first, and only touches its password
# when explicitly asked to). Meant for the first deploy, or for
# regaining admin access without SSH-ing in with a TTY for
# `manage.py createsuperuser`'s interactive prompts.
#
# Env vars:
#   APP_DIR=/srv/shoporbit
#   SUPERUSER_EMAIL       Required.
#   SUPERUSER_PASSWORD    Required.
#   SUPERUSER_FULL_NAME=Admin

set -euo pipefail

APP_DIR="${APP_DIR:-/srv/shoporbit}"
VENV_DIR="$APP_DIR/shared/venv"
CURRENT_LINK="$APP_DIR/current"

if [[ -z "${SUPERUSER_EMAIL:-}" || -z "${SUPERUSER_PASSWORD:-}" ]]; then
    echo "SUPERUSER_EMAIL and SUPERUSER_PASSWORD must both be set." >&2
    exit 1
fi

SUPERUSER_FULL_NAME="${SUPERUSER_FULL_NAME:-Admin}"

cd "$CURRENT_LINK"
DJANGO_SETTINGS_MODULE=config.settings.production "$VENV_DIR/bin/python" manage.py shell <<PYEOF
from accounts.models import User

email = "$SUPERUSER_EMAIL"
password = "$SUPERUSER_PASSWORD"
full_name = "$SUPERUSER_FULL_NAME"

user, created = User.objects.get_or_create(
    email=email, defaults={"full_name": full_name, "is_staff": True, "is_superuser": True}
)
if not created:
    user.is_staff = True
    user.is_superuser = True
    user.full_name = full_name

user.set_password(password)
user.save()
print(f"{'Created' if created else 'Updated'} superuser: {email}")
PYEOF
