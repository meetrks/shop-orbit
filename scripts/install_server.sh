#!/usr/bin/env bash
# One-time Ubuntu 24.04 LTS server setup: installs every system package
# the app needs, creates the deploy user and release-tree directories,
# installs the systemd units and Nginx config, and applies baseline
# firewall/intrusion-prevention hardening.
#
# Idempotent — safe to re-run after a package/config update. Run as root
# (or via sudo). See docs/SERVER_SETUP.md for what each step does and why.
#
# Env vars (all optional, sensible defaults shown):
#   APP_DIR=/srv/shoporbit    Release tree root.
#   DEPLOY_USER=deploy             Dedicated non-root system user everything runs as.
#   DOMAIN=shoporbit.example       Used only to print a reminder at the end — DNS/Certbot
#                                  still need to be set up manually per docs/DEPLOYMENT.md.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root (sudo bash scripts/install_server.sh)." >&2
    exit 1
fi

APP_DIR="${APP_DIR:-/srv/shoporbit}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
DOMAIN="${DOMAIN:-shoporbit.example}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Updating apt and installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
    python3.11 python3.11-venv python3-pip \
    postgresql postgresql-contrib \
    redis-server \
    nginx \
    certbot python3-certbot-nginx \
    git curl rsync \
    ufw fail2ban \
    nodejs npm \
    build-essential libpq-dev

echo "==> Installing uv (if not already present)"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv installs to ~/.local/bin for whichever user runs the installer —
    # symlink it somewhere already on root's/every user's PATH.
    ln -sf "$HOME/.local/bin/uv" /usr/local/bin/uv
fi

echo "==> Creating the '$DEPLOY_USER' system user"
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$DEPLOY_USER"
fi

echo "==> Creating the release tree at $APP_DIR"
mkdir -p "$APP_DIR/releases" "$APP_DIR/shared/media" "$APP_DIR/backups" "$APP_DIR/scripts"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"

echo "==> Seeding $APP_DIR/scripts (the stable entry point deploy.yml calls, refreshed on every deploy.sh run)"
cp "$REPO_ROOT"/scripts/*.sh "$APP_DIR/scripts/"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR/scripts"

if [[ ! -f "$APP_DIR/shared/.env" ]]; then
    echo "==> No $APP_DIR/shared/.env yet — copying .env.example as a starting point."
    echo "    You MUST edit it with real production values before the first deploy."
    cp "$REPO_ROOT/.env.example" "$APP_DIR/shared/.env"
    chown "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR/shared/.env"
    chmod 600 "$APP_DIR/shared/.env"
fi

echo "==> Enabling PostgreSQL and Redis"
systemctl enable --now postgresql
systemctl enable --now redis-server

echo "==> Installing systemd units"
cp "$REPO_ROOT/deploy/systemd/gunicorn.service" /etc/systemd/system/gunicorn.service
cp "$REPO_ROOT/deploy/systemd/celery-worker.service" /etc/systemd/system/celery-worker.service
cp "$REPO_ROOT/deploy/systemd/celery-beat.service" /etc/systemd/system/celery-beat.service
systemctl daemon-reload
systemctl enable gunicorn celery-worker celery-beat

echo "==> Installing Nginx site config"
cp "$REPO_ROOT/deploy/nginx/shoporbit.conf" /etc/nginx/sites-available/shoporbit
ln -sf /etc/nginx/sites-available/shoporbit /etc/nginx/sites-enabled/shoporbit
rm -f /etc/nginx/sites-enabled/default
mkdir -p /var/www/certbot
nginx -t && systemctl reload nginx || echo "WARNING: nginx -t failed — likely because the TLS cert doesn't exist yet. Run scripts/renew_ssl.sh after DNS is pointed at this server, then re-run 'nginx -t'."

echo "==> Configuring UFW (allow SSH/HTTP/HTTPS, deny everything else)"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> Configuring Fail2ban (sshd jail)"
cat > /etc/fail2ban/jail.local <<'EOF'
[sshd]
enabled = true
bantime = 1h
findtime = 10m
maxretry = 5
EOF
systemctl enable --now fail2ban
systemctl restart fail2ban

cat <<EOF

==> install_server.sh done.

Next steps (see docs/DEPLOYMENT.md for the full walkthrough):
  1. Point DNS for $DOMAIN at this server's IP.
  2. Edit $APP_DIR/shared/.env with real production secrets.
  3. Create the production database/role (see docs/infrastructure.md's
     local setup section for the CREATE ROLE/CREATE DATABASE commands —
     same idea, different host).
  4. Run scripts/deploy.sh to ship the first release.
  5. Run scripts/renew_ssl.sh (wraps 'certbot --nginx') once DNS has propagated.
EOF
