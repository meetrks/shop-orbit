# Deployment

Full walkthrough for deploying shoporbit to a self-managed Ubuntu
24.04 LTS VPS via native systemd (no Docker) — Gunicorn behind Nginx,
Celery worker + beat, PostgreSQL and Redis as Ubuntu system packages.

This has been built and verified locally (settings load cleanly, health
endpoints respond, Gunicorn serves the app, all scripts pass a syntax
check) but **has not been run against a real server** — no VPS/domain
exists yet for this project. Everything below is correct and ready to
run; treat the first real run of each step as the actual first test of
it. See `docs/SERVER_SETUP.md` for what `install_server.sh` does in
detail, and `docs/TROUBLESHOOTING.md` if something doesn't come up
cleanly.

## 1. Provision the server

Any Ubuntu 24.04 LTS VPS (Hetzner, DigitalOcean, Hostinger, EC2...) with
at least 1 vCPU / 2GB RAM works for a small storefront. Note its public
IP.

## 2. Point DNS at it

Create an `A` record for your domain (and `www`) pointing at the
server's IP. DNS propagation can take a few minutes to a few hours —
Certbot (step 6) will fail until it's actually resolving.

## 3. Run the server setup script

```sh
git clone <your-repo-url> /tmp/shoporbit-setup
cd /tmp/shoporbit-setup
sudo DOMAIN=yourdomain.com bash scripts/install_server.sh
```

This installs every system package (Python, PostgreSQL, Redis, Nginx,
Certbot, Node, UFW, Fail2ban...), creates the `deploy` system user and
the `/srv/shoporbit` release tree, installs the systemd units and
Nginx config, seeds `/srv/shoporbit/shared/.env` from
`.env.example`, and applies baseline firewall/intrusion-prevention
rules. Idempotent — safe to re-run.

## 4. Configure secrets

Edit `/srv/shoporbit/shared/.env` with real production values —
`DJANGO_SECRET_KEY` (generate a fresh one, don't reuse the one from
`.env.example`), `DJANGO_ALLOWED_HOSTS`, `DATABASE_URL`, Razorpay keys,
SMTP credentials, etc. See `docs/ENVIRONMENT_VARIABLES.md` for every
variable.

## 5. Create the production database

```sh
sudo -u postgres psql -c "CREATE ROLE shoporbit WITH LOGIN PASSWORD 'change-me';"
sudo -u postgres psql -c "CREATE DATABASE shoporbit OWNER shoporbit ENCODING 'UTF8';"
```

Match `DATABASE_URL` in `.env` to whatever role/password/database name
you actually created.

## 6. First deploy

```sh
sudo -u deploy GIT_REPO_URL=<your-repo-url> bash /srv/shoporbit/scripts/deploy.sh
```

This clones the app, syncs Python dependencies into a shared venv,
builds the Tailwind/HTMX/Alpine frontend assets, runs migrations,
collects static files, atomically switches the `current` symlink,
restarts Gunicorn/Celery, and health-checks the result — automatically
rolling back if anything fails. See `docs/CI_CD.md` for wiring this to
run automatically on every push to `main` instead of by hand.

At this point the site is reachable over plain HTTP (Nginx is
configured, but `certbot --nginx` hasn't run yet, so there's no
certificate).

## 7. Issue the TLS certificate

```sh
sudo DOMAIN=yourdomain.com bash /srv/shoporbit/scripts/renew_ssl.sh --issue
```

This is Certbot's first-ever issuance for the domain — it also rewrites
the Nginx config to redirect HTTP to HTTPS automatically. Ubuntu's
`certbot` package installs its own systemd timer for renewal; the same
script (without `--issue`) is the manual/cron fallback — see
`docs/DEPLOYMENT.md`'s note in `scripts/renew_ssl.sh`'s own header
comment.

## 8. Create an admin account

```sh
sudo -u deploy SUPERUSER_EMAIL=you@yourdomain.com SUPERUSER_PASSWORD='change-me' \
    bash /srv/shoporbit/scripts/create_superuser.sh
```

## 9. Verify

```sh
sudo bash /srv/shoporbit/scripts/health_check.sh
sudo systemctl status gunicorn celery-worker celery-beat nginx
```

Visit `https://yourdomain.com/admin/` and confirm you can log in.

## Ongoing operations

- **Deploys**: re-run `scripts/deploy.sh` (or push to `main` if
  `.github/workflows/deploy.yml`'s secrets are configured — see
  `docs/CI_CD.md`).
- **Rollback**: `scripts/rollback.sh` (automatic on a failed deploy;
  runnable by hand too).
- **Backups**: `scripts/backup.sh`, ideally on a daily cron/systemd timer
  — see `docs/BACKUP.md`.
- **Dependency upgrades**: `scripts/update_dependencies.sh`, run
  deliberately, not automatically.
- **Monitoring**: `docs/MONITORING.md`.

## What this initiative deliberately did NOT build

Called out explicitly rather than silently oversold:

- **pgbouncer / real connection pooling** — `CONN_MAX_AGE` gives basic
  connection reuse, appropriate for a single-VPS deployment; a real
  pooler is a future upgrade once traffic justifies it.
- **Brotli compression in Nginx** — not in Ubuntu's stock Nginx package
  without a third-party module rebuild; gzip is enabled instead.
- **A hardened Content-Security-Policy** — the CSP in
  `deploy/nginx/shoporbit.conf` is deliberately loose
  (`unsafe-inline`) to avoid breaking HTMX/Alpine's inline usage;
  tightening it to a nonce-based policy is real follow-up work.
- **Prometheus metrics** — the two HTTP health endpoints plus
  `scripts/health_check.sh` cover liveness/readiness; a metrics exporter
  is a reasonable future addition, not built here.
- **Content-hashed static filenames** (e.g. Django's
  `ManifestStaticFilesStorage`) — Nginx's static-file cache headers are a
  moderate `max-age`, not `immutable`, as a direct consequence.
