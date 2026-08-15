"""
Base Django settings for the shoporbit project — shared by every
environment. `development.py` and `production.py` both start with
`from .base import *` and only override what genuinely differs.

Configuration values that differ between environments (secret key, debug
flag, allowed hosts, database URL) are pulled from environment variables via
django-environ, backed by a local `.env` file that is never committed to
version control. See `.env.example` for the full list of expected keys.
"""

import sys
from decimal import Decimal
from pathlib import Path

import environ
from celery.schedules import crontab

# Three levels up from config/settings/base.py: settings/ -> config/ -> repo root.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)

# Reads the `.env` file that sits next to manage.py, if present. In real
# production deployments the same variables would instead be exported
# directly into the process environment (see deploy/systemd/*.service's
# EnvironmentFile= directive).
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")

DEBUG = env.bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "shoporbit.example"],
)

# Origins allowed to POST here (login, checkout, etc.) despite Django seeing a
# different Host header than what the browser's address bar shows — needed
# whenever the site is accessed through a tunnel like ngrok. Wildcard
# subdomains use Django's `https://*.example.com` syntax, not a leading dot.
CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=["https://*.ngrok-free.dev", "https://*.ngrok-free.app", "https://*.ngrok.io"],
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.postgres",
    "django.contrib.sitemaps",
    "crispy_forms",
    "crispy_tailwind",
    "rest_framework",
    "eav",
    "auditlog",
    "accounts",
    "catalog",
    "cart",
    "payments",
    "fulfillment",
    "inventory",
    "returns",
    "picweight",
    "pages",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Must come after AuthenticationMiddleware — it reads request.user to
    # record who made each change (see common/audit.py for what's tracked).
    "auditlog.middleware.AuditlogMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "catalog.context_processors.taxonomy_nav",
                "cart.context_processors.cart_summary",
                "common.context_processors.site_config",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Asia/Kolkata"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Primary key type for django-eav2's internal tables (eav.Attribute/Value/…).
EAV2_PRIMARY_KEY_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:profile"
LOGOUT_REDIRECT_URL = "pages:home"

CRISPY_ALLOWED_TEMPLATE_PACKS = "tailwind"
CRISPY_TEMPLATE_PACK = "tailwind"

# Maximum size, in bytes, accepted for a single PicWeight source image
# upload (8 MB). Enforced in picweight.forms.SupplierImageUploadForm.
PICWEIGHT_MAX_UPLOAD_SIZE = 8 * 1024 * 1024

# ---------------------------------------------------------------------------
# Site branding, company details, and SEO defaults.
#
# Every one of these is overridable from `.env` so the same codebase can be
# re-skinned for a different storefront name/domain without touching Python
# or template code. `common.context_processors.site_config` exposes them to
# every template as `site`; templates never hardcode "Shop Orbit".
# ---------------------------------------------------------------------------
SITE_NAME = env("SITE_NAME", default="Shop Orbit")
SITE_LEGAL_NAME = env("SITE_LEGAL_NAME", default=SITE_NAME)
SITE_TAGLINE = env("SITE_TAGLINE", default="Quality products, thoughtfully curated for every need.")
SITE_DOMAIN = env("SITE_DOMAIN", default="shoporbit.example")
SITE_SCHEME = env("SITE_SCHEME", default="https")

# Relative to STATIC_URL, e.g. "img/logo.png". Leave blank to fall back to a
# generated initials badge derived from SITE_NAME.
SITE_LOGO_PATH = env("SITE_LOGO_PATH", default="")

# Prefix for generated order numbers (see cart.models.Order._generate_order_number),
# e.g. "ORD" -> "ORD38301240".
ORDER_NUMBER_PREFIX = env("ORDER_NUMBER_PREFIX", default="ORD")

COMPANY_ADDRESS_LINE1 = env("COMPANY_ADDRESS_LINE1", default="")
COMPANY_ADDRESS_LINE2 = env("COMPANY_ADDRESS_LINE2", default="")
COMPANY_CITY = env("COMPANY_CITY", default="")
COMPANY_STATE = env("COMPANY_STATE", default="")
COMPANY_POSTAL_CODE = env("COMPANY_POSTAL_CODE", default="")
COMPANY_COUNTRY = env("COMPANY_COUNTRY", default="India")
COMPANY_PHONE = env("COMPANY_PHONE", default="")
COMPANY_EMAIL = env("COMPANY_EMAIL", default="")
COMPANY_SUPPORT_HOURS = env("COMPANY_SUPPORT_HOURS", default="")
COMPANY_GST_NUMBER = env("COMPANY_GST_NUMBER", default="")

# Grievance officer details, as required for e-commerce entities under
# India's Consumer Protection (E-Commerce) Rules, 2020 and IT Rules, 2021.
# Left blank by default — the Grievance section on the policy pages only
# renders once a real named officer is configured, rather than publishing
# a placeholder that wouldn't satisfy the actual legal requirement.
GRIEVANCE_OFFICER_NAME = env("GRIEVANCE_OFFICER_NAME", default="")
GRIEVANCE_OFFICER_EMAIL = env("GRIEVANCE_OFFICER_EMAIL", default="")
GRIEVANCE_OFFICER_PHONE = env("GRIEVANCE_OFFICER_PHONE", default="")
GRIEVANCE_RESPONSE_DAYS = env.int("GRIEVANCE_RESPONSE_DAYS", default=30)

# Google Analytics 4 + Search Console. Both blank by default — gtag.js is
# only loaded (from Google's CDN; it has to report back to Google, so it
# can't be vendored like htmx/Alpine) once GA_MEASUREMENT_ID is set, and
# the verification meta tag only renders once GOOGLE_SITE_VERIFICATION is
# set. See templates/base.html.
GA_MEASUREMENT_ID = env("GA_MEASUREMENT_ID", default="")
GOOGLE_SITE_VERIFICATION = env("GOOGLE_SITE_VERIFICATION", default="")

SEO_DEFAULT_TITLE = env("SEO_DEFAULT_TITLE", default=f"{SITE_NAME} — Online Store")
SEO_DEFAULT_DESCRIPTION = env(
    "SEO_DEFAULT_DESCRIPTION",
    default=(f"{SITE_NAME} — quality products, thoughtfully curated. Browse our full catalog and shop online."),
)
SEO_DEFAULT_KEYWORDS = env(
    "SEO_DEFAULT_KEYWORDS",
    default="online store, shopping, ecommerce, India",
)
# Relative to STATIC_URL, e.g. "img/og-default.jpg". Leave blank to omit the
# og:image / twitter:image tags on pages that don't set their own.
SEO_DEFAULT_OG_IMAGE_PATH = env("SEO_DEFAULT_OG_IMAGE_PATH", default="")
SEO_TWITTER_HANDLE = env("SEO_TWITTER_HANDLE", default="")

DJANGO_ADMIN_SITE_HEADER = env("DJANGO_ADMIN_SITE_HEADER", default=f"{SITE_NAME} Admin")
DJANGO_ADMIN_SITE_TITLE = env("DJANGO_ADMIN_SITE_TITLE", default=f"{SITE_NAME} Admin Portal")

# ---------------------------------------------------------------------------
# Razorpay: the only payment gateway wired in today, reached exclusively
# through the `payments` app's gateway abstraction
# (`payments.gateways.get_gateway()`) — see payments/gateways/base.py.
# Adding a second gateway later means adding another entry to
# `payments.gateways.GATEWAY_CLASSES`, not touching this section's shape.
# ---------------------------------------------------------------------------
DEFAULT_PAYMENT_GATEWAY = env("DEFAULT_PAYMENT_GATEWAY", default="razorpay")
RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID", default="")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET", default="")
# Set in the Razorpay dashboard under Settings > Webhooks when creating the
# webhook pointed at /payments/razorpay/webhook/ — used to verify that
# webhook deliveries actually came from Razorpay.
RAZORPAY_WEBHOOK_SECRET = env("RAZORPAY_WEBHOOK_SECRET", default="")

# Prefix for generated GST invoice numbers, e.g. "INV" -> "INV/2025-26/000123".
INVOICE_SERIES_PREFIX = env("INVOICE_SERIES_PREFIX", default="INV")

# Storefront WhatsApp number for the click-to-chat button, in international
# format without spaces or a leading "+" (e.g. 919876543210). Falls back to
# COMPANY_PHONE with non-digits stripped if left blank.
WHATSAPP_NUMBER = env("WHATSAPP_NUMBER", default="")

# ---------------------------------------------------------------------------
# Outbound email (order confirmations, status updates, account notices).
#
# Like every other environment-specific value in this file, credentials are
# read from `.env` and never hardcoded or stored in the database — see
# `.env.example` for the full list of keys. In development, with no SMTP
# host configured, mail is printed to the console instead of sent.
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default=(
        "django.core.mail.backends.smtp.EmailBackend"
        if env("EMAIL_HOST", default="")
        else "django.core.mail.backends.console.EmailBackend"
    ),
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)

# The address every outbound notification is sent from / bounces go to.
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default=f"{SITE_NAME} <no-reply@{SITE_DOMAIN}>")
SERVER_EMAIL = env("SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)

# Staff mailbox that gets a copy of operational notices (new contact-form
# enquiries, new orders placed). Leave blank to disable staff-side emails.
STAFF_NOTIFICATION_EMAIL = env("STAFF_NOTIFICATION_EMAIL", default=COMPANY_EMAIL)

# Where a customer's "Reply" on a transactional email actually lands.
# DEFAULT_FROM_EMAIL is typically a no-reply@ address that nothing reads —
# this defaults to the support inbox instead so replies aren't silently
# lost. Leave blank to omit the header entirely (replies then go to
# DEFAULT_FROM_EMAIL, whatever that resolves to for the receiving client).
REPLY_TO_EMAIL = env("REPLY_TO_EMAIL", default=COMPANY_EMAIL)

# ---------------------------------------------------------------------------
# Celery — background work (currently: sending the emails above; see
# common/tasks.py). Broker is Redis; no result backend is configured since
# nothing currently needs to wait on a task's return value — add one if a
# future task does.
#
# CELERY_TASK_ALWAYS_EAGER defaults to on under `manage.py test` so the test
# suite exercises task code synchronously, in-process, without needing a
# running worker or broker — every existing test that asserts on
# `mail.outbox` right after triggering a notification keeps working
# unmodified. Real environments (runserver, production) always run tasks
# through an actual worker.
#
# Queue routing (default vs. emails) and production-only tuning
# (CELERY_TASK_ACKS_LATE, prefetch, retry-on-startup) live in
# config/settings/production.py, not here — development doesn't need them.
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default="test" in sys.argv)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# Periodic tasks. A plain settings-dict schedule is enough for this one
# fixed-interval job — reach for django-celery-beat instead if/when a
# future task needs an admin-editable schedule.
CELERY_BEAT_SCHEDULE = {
    "release-expired-stock-reservations": {
        "task": "inventory.tasks.release_expired_reservations",
        "schedule": crontab(minute="*/5"),
    },
    # No-ops entirely until DELHIVERY_API_TOKEN is set — see fulfillment/tasks.py.
    "sync-delhivery-shipment-tracking": {
        "task": "fulfillment.tasks.sync_all_shipment_tracking",
        "schedule": crontab(minute="*/30"),
    },
    "cancel-stale-unpaid-orders": {
        "task": "cart.tasks.cancel_stale_unpaid_orders",
        "schedule": crontab(minute=0),
    },
}

# How long an order can sit AWAITING_PAYMENT — holding its checkout's
# stock reservation — before inventory.tasks.release_expired_reservations
# gives it up and marks the order EXPIRED. Comfortably longer than a
# normal Razorpay Checkout session should ever take.
RESERVATION_TIMEOUT_MINUTES = env.int("RESERVATION_TIMEOUT_MINUTES", default=30)

# How long an order can sit AWAITING_PAYMENT or PAYMENT_FAILED before
# cart.tasks.cancel_stale_unpaid_orders formally cancels it. Much longer
# than RESERVATION_TIMEOUT_MINUTES on purpose — that sweep only lets go of
# the stock hold; this one closes the order out for good, so it gives a
# buyer a full day to retry before that happens.
ORDER_CANCEL_TIMEOUT_HOURS = env.int("ORDER_CANCEL_TIMEOUT_HOURS", default=24)

# How many days after delivery a buyer can request a return. See
# returns.services.request_return.
RETURN_WINDOW_DAYS = env.int("RETURN_WINDOW_DAYS", default=7)

# ---------------------------------------------------------------------------
# Delhivery courier integration (see fulfillment/couriers/delhivery.py).
# DELHIVERY_API_TOKEN blank by default — every Delhivery-calling code path
# no-ops (or raises a clear "not configured" error, for actions a staff
# member explicitly triggered) until a real token is set, same pattern as
# Razorpay/Sentry/GA4. Get the token and pickup location name from
# Delhivery once your API access is provisioned; verify DELHIVERY_BASE_URL
# and the endpoints in fulfillment/couriers/delhivery.py against Delhivery's
# current developer docs before going live — this was built without a live
# account to test against, so it needs verification once you have one.
# ---------------------------------------------------------------------------
DELHIVERY_API_TOKEN = env("DELHIVERY_API_TOKEN", default="")
DELHIVERY_BASE_URL = env("DELHIVERY_BASE_URL", default="https://track.delhivery.com")
DELHIVERY_PICKUP_LOCATION_NAME = env("DELHIVERY_PICKUP_LOCATION_NAME", default="")
# Used as every shipment's declared weight, since products don't currently
# carry their own weight — fine for small/light goods (jewellery,
# accessories); revisit with a per-product weight field if the catalog
# later includes meaningfully heavier items.
DELHIVERY_DEFAULT_PACKAGE_WEIGHT_GRAMS = env.int("DELHIVERY_DEFAULT_PACKAGE_WEIGHT_GRAMS", default=500)

# ---------------------------------------------------------------------------
# Profitability reporting (see catalog/profitability.py, the Store
# Dashboard's "Profitability" report). None of these costs are tracked
# per-transaction yet — they're store-wide estimates applied uniformly, a
# reasonable starting point given actual per-order gateway/courier costs
# aren't currently captured anywhere. Revisit with real per-order figures
# (e.g. from Razorpay settlement reports, real Delhivery invoicing) once
# volume makes the estimate worth replacing.
# ---------------------------------------------------------------------------
# Razorpay's blended fee across UPI/cards/net banking/wallets, inclusive of
# the 18% GST charged on the fee itself — adjust to your actual negotiated
# rate once you have real settlement data.
PAYMENT_GATEWAY_FEE_PERCENT = env.float("PAYMENT_GATEWAY_FEE_PERCENT", default=2.36)
# What it actually costs to ship one order (courier charges to you, not
# Product.delivery_charge, which is what you charge the buyer). 0 means
# "not tracked yet" — the report shows this plainly rather than guessing.
DEFAULT_SHIPPING_COST_PER_ORDER = Decimal(env("DEFAULT_SHIPPING_COST_PER_ORDER", default="0.00"))
DEFAULT_PACKAGING_COST_PER_UNIT = Decimal(env("DEFAULT_PACKAGING_COST_PER_UNIT", default="0.00"))

# ---------------------------------------------------------------------------
# Django REST Framework — installed and configured with a secure-by-default
# baseline, but no endpoints exist yet. Per project convention, APIs get
# added only where one is actually needed (most dynamic UI is served via
# HTMX partials instead); this section exists so the first real endpoint
# has sane defaults to inherit rather than starting from nothing.
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# ---------------------------------------------------------------------------
# Sentry error monitoring. Off by default (SENTRY_DSN blank) so local dev
# never reports anywhere — set SENTRY_DSN in production's environment to
# turn this on. Covers Django (request-cycle exceptions) and Celery (task
# failures) automatically via their integrations. LoggingIntegration's
# event_level=WARNING also turns every existing logger.warning()/exception()
# call app-wide into a Sentry event — including the payment/webhook
# signature-rejection and processing failures already logged at those
# levels in payments/services.py and payments/views.py — with no code
# changes needed there.
# ---------------------------------------------------------------------------
SENTRY_DSN = env("SENTRY_DSN", default="")
SENTRY_ENVIRONMENT = env("SENTRY_ENVIRONMENT", default="development" if DEBUG else "production")

if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=SENTRY_ENVIRONMENT,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
            # WARNING-and-above log records (e.g. django.security's logger,
            # already wired to mail_admins in production.py's LOGGING) are
            # also reported as Sentry breadcrumbs/events — a second channel
            # on top of, not a replacement for, the admin emails.
            LoggingIntegration(level=None, event_level="WARNING"),
        ],
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
        send_default_pii=False,
    )
