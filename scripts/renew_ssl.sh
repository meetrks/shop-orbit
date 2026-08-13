#!/usr/bin/env bash
# Thin wrapper around Certbot. Ubuntu's certbot package already installs
# its own systemd timer (`systemctl list-timers | grep certbot`) that runs
# renewal twice a day automatically — this script is the manual/cron
# fallback and what you run once, by hand, for the very first issuance.
#
# First-ever run (after DNS points at this server and Nginx is already
# serving plain HTTP — see scripts/install_server.sh):
#   sudo scripts/renew_ssl.sh --issue
#
# Routine renewal (what the systemd timer effectively runs):
#   sudo scripts/renew_ssl.sh

set -euo pipefail

DOMAIN="${DOMAIN:-shoporbit.example}"
WWW_DOMAIN="${WWW_DOMAIN:-www.$DOMAIN}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@$DOMAIN}"

if [[ "${1:-}" == "--issue" ]]; then
    echo "==> Requesting a new certificate for $DOMAIN, $WWW_DOMAIN (notifications to $CERTBOT_EMAIL)"
    certbot --nginx -d "$DOMAIN" -d "$WWW_DOMAIN" --non-interactive --agree-tos -m "$CERTBOT_EMAIL" --redirect
else
    echo "==> Renewing existing certificates (no-op if not yet due)"
    certbot renew --quiet
fi

systemctl reload nginx
