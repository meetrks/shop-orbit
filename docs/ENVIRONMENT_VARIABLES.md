# Environment Variables

Every environment variable the app reads, in one place. `.env.example` is
the authoritative, copy-pasteable source (with comments inline) — this
document groups them by purpose and calls out which ones are
production-only. See `config/settings/base.py` (shared),
`config/settings/development.py`, and `config/settings/production.py` for
exactly how each is consumed.

## Core Django

| Variable | Default | Notes |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.development` | Only set explicitly in production (`config.settings.production`) — see `deploy/systemd/*.service`. |
| `DJANGO_SECRET_KEY` | none (required) | Generate with `uv run python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`. Never reuse across environments. |
| `DJANGO_DEBUG` | `True` | Ignored by `config.settings.production`, which hardcodes `DEBUG=False` regardless of this value — it only affects the development settings module. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,shoporbit.example` | Comma-separated. Set to your real domain(s) in production. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | ngrok wildcards (dev only) | Comma-separated `https://domain` origins. Set to your real `https://yourdomain` in production. |

## Database

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///db.sqlite3` | `postgres://user:password@host:5432/dbname` in every real environment — see `docs/infrastructure.md`. |
| `DB_CONN_MAX_AGE` | `60` (production only) | Seconds a DB connection is reused across requests within one Gunicorn worker. |
| `DB_CONNECT_TIMEOUT` | `10` (production only) | Seconds before a new DB connection attempt gives up. |

## Cache / Celery (production only unless noted)

| Variable | Default | Notes |
|---|---|---|
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Used in every environment. |
| `REDIS_CACHE_URL` | `redis://localhost:6379/1` | Deliberately a different logical Redis DB than the broker — see `config/settings/production.py`. |
| `CELERY_WORKER_PREFETCH_MULTIPLIER` | `4` | How many tasks a worker process reserves ahead of time. |

## Transport security (production only)

| Variable | Default | Notes |
|---|---|---|
| `DJANGO_SECURE_SSL_REDIRECT` | `True` | Redirect all HTTP to HTTPS. |
| `DJANGO_SECURE_HSTS_SECONDS` | `31536000` (1 year) | Start lower (e.g. `300`) on first rollout — see `docs/SECURITY.md`. |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True` | |
| `DJANGO_SECURE_HSTS_PRELOAD` | `False` | Submitting to the browser preload list is close to irreversible — opt in deliberately, not by default. |
| `DJANGO_ADMINS` | none | Comma-separated `Name:email` pairs, emailed on uncaught server errors. |

## Reservation / return-window business rules

| Variable | Default | Notes |
|---|---|---|
| `RESERVATION_TIMEOUT_MINUTES` | `30` | See `inventory/tasks.py`. |
| `RETURN_WINDOW_DAYS` | `7` | See `returns/services.py`. |

## Payments (Razorpay)

| Variable | Default | Notes |
|---|---|---|
| `DEFAULT_PAYMENT_GATEWAY` | `razorpay` | |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | `""` | From https://dashboard.razorpay.com/app/keys. Use test-mode keys until going live. |
| `RAZORPAY_WEBHOOK_SECRET` | `""` | Set when creating the webhook (Settings > Webhooks) pointed at `/payments/razorpay/webhook/`. |
| `INVOICE_SERIES_PREFIX` | `INV` | |

## Email

| Variable | Default | Notes |
|---|---|---|
| `EMAIL_HOST` | `""` | Blank = console backend (mail printed, not sent) — fine for dev, must be set in production. |
| `EMAIL_PORT` | `587` | |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | `""` | |
| `EMAIL_USE_TLS` / `EMAIL_USE_SSL` | `True` / `False` | |
| `EMAIL_TIMEOUT` | `10` | |
| `DEFAULT_FROM_EMAIL` | `{SITE_NAME} <no-reply@{SITE_DOMAIN}>` | |
| `STAFF_NOTIFICATION_EMAIL` | `COMPANY_EMAIL` | CC'd on new orders / contact-form enquiries. |
| `REPLY_TO_EMAIL` | `COMPANY_EMAIL` | Where a customer's "Reply" on a transactional email lands. Set to `""` to omit the header. |

**Deliverability (SPF/DKIM/DMARC)** — DNS records, not env vars, so they're
configured with your domain registrar/DNS host, not here. Whichever SMTP
provider you set `EMAIL_HOST` to (SES, Postmark, SendGrid, etc.) will give
you the exact records to add:

- **SPF** — a TXT record on `SITE_DOMAIN` authorizing that provider to send
  as your domain.
- **DKIM** — the provider gives you one or more CNAME/TXT records to add;
  this is what actually signs outbound mail.
- **DMARC** — a `_dmarc.SITE_DOMAIN` TXT record declaring a policy (start
  with `p=none` to monitor without rejecting, tighten later).

Skipping these doesn't stop mail from sending, but it does get transactional
emails (order confirmations, password resets) flagged as spam or rejected
outright by Gmail/Outlook — treat this as required, not optional, before
launch. Most providers also have a "test your setup" tool (e.g. mail-tester.com)
worth running once configured.

## Site branding, company details, SEO defaults

All optional, all covered with inline comments in `.env.example`: `SITE_NAME`,
`SITE_LEGAL_NAME`, `SITE_TAGLINE`, `SITE_DOMAIN`, `SITE_SCHEME`,
`SITE_LOGO_PATH`, `COMPANY_ADDRESS_LINE1/2`, `COMPANY_CITY`,
`COMPANY_STATE`, `COMPANY_POSTAL_CODE`, `COMPANY_COUNTRY`,
`COMPANY_PHONE`, `COMPANY_EMAIL`, `COMPANY_SUPPORT_HOURS`,
`COMPANY_GST_NUMBER`, `SEO_DEFAULT_TITLE`, `SEO_DEFAULT_DESCRIPTION`,
`SEO_DEFAULT_KEYWORDS`, `SEO_DEFAULT_OG_IMAGE_PATH`, `SEO_TWITTER_HANDLE`,
`DJANGO_ADMIN_SITE_HEADER`, `DJANGO_ADMIN_SITE_TITLE`, `WHATSAPP_NUMBER`.

`SITE_VERSION` is separate from the branding vars above — it doesn't
default to a fixed value in `.env.example`, only in code (see
`config/settings/base.py`). It's what busts the PWA service worker's
static-asset cache on deploy (`templates/sw.js`'s `CACHE_NAME`); leave it
unset unless you want to override the deploy-directory-name default with
something like a git commit SHA.

## Deployment scripts (not read by Django itself)

These are read by `scripts/*.sh`, not by any Django settings module:

| Variable | Used by | Notes |
|---|---|---|
| `APP_DIR` | all scripts | Default `/srv/shoporbit`. |
| `DEPLOY_USER` | `install_server.sh` | Default `deploy`. |
| `DOMAIN` | `install_server.sh`, `renew_ssl.sh` | |
| `GIT_REPO_URL` | `deploy.sh` | Required — no default, deploy.sh refuses to run without it. |
| `GIT_REF` | `deploy.sh` | Default `main`. |
| `KEEP_RELEASES` | `deploy.sh` | Default `5`. |
| `RETENTION_DAYS` | `backup.sh` | Default `14`. |
| `CERTBOT_EMAIL` | `renew_ssl.sh` | Default `admin@$DOMAIN`. |
| `SUPERUSER_EMAIL` / `SUPERUSER_PASSWORD` / `SUPERUSER_FULL_NAME` | `create_superuser.sh` | Required (first two). |
| `HEALTH_CHECK_URL` / `SKIP_CELERY_CHECK` | `health_check.sh` | |
| `GUNICORN_WORKERS` / `GUNICORN_THREADS` / `GUNICORN_TIMEOUT` / `GUNICORN_GRACEFUL_TIMEOUT` / `GUNICORN_KEEPALIVE` / `GUNICORN_MAX_REQUESTS` / `GUNICORN_MAX_REQUESTS_JITTER` / `GUNICORN_WORKER_TMP_DIR` / `GUNICORN_LOG_LEVEL` / `GUNICORN_SOCKET` | `gunicorn.conf.py` | All optional, sensible defaults — see that file. |

## GitHub Actions secrets (CI/CD only, not env vars on the server)

Set under repo Settings > Secrets and variables > Actions:

| Secret | Used by |
|---|---|
| `VPS_HOST` | `.github/workflows/deploy.yml` |
| `VPS_USER` | `.github/workflows/deploy.yml` |
| `VPS_SSH_KEY` | `.github/workflows/deploy.yml` |
| `GIT_REPO_URL` | `.github/workflows/deploy.yml` (passed through to `deploy.sh`) |
