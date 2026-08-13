"""
Production settings — used by Gunicorn (see gunicorn.conf.py /
deploy/systemd/gunicorn.service) and the Celery worker/beat systemd units,
all of which set `DJANGO_SETTINGS_MODULE=config.settings.production`
explicitly via their `EnvironmentFile=`. Never used implicitly — see
`manage.py`/`config/wsgi.py`/`config/asgi.py`, which all default to
`config.settings.development`.

Nginx terminates TLS and reverse-proxies to Gunicorn over plain HTTP on a
Unix socket (see deploy/nginx/shoporbit.conf) — every setting below
that assumes "the request was HTTPS" only works because of that proxy
setup, not because Django itself terminates TLS.
"""

from .base import *  # noqa: F401,F403
from .base import env

# Hard override, not just a default: production must never run with DEBUG
# on even if DJANGO_DEBUG is misconfigured in the environment.
DEBUG = False

# ---------------------------------------------------------------------------
# Transport security. SECURE_PROXY_SSL_HEADER trusts Nginx's
# X-Forwarded-Proto header — only safe because Nginx (not some
# user-controlled client) is the only thing that can reach Gunicorn's
# socket, so a client can't spoof this header directly.
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

# HSTS: start low (e.g. 300 seconds) on first rollout to confirm HTTPS is
# solid before committing browsers to a year-long HSTS lock-in, then raise
# via DJANGO_SECURE_HSTS_SECONDS once confident — see docs/SECURITY.md.
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)

# ---------------------------------------------------------------------------
# Cache — Django's built-in Redis backend (no extra dependency). Deliberately
# a different logical Redis DB than the Celery broker (DB 0, see
# CELERY_BROKER_URL in base.py) so cache eviction can never touch broker
# data. Used for the readiness health check (see common/health.py) and is
# available to any view/template-fragment caching added later.
# ---------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_CACHE_URL", default="redis://localhost:6379/1"),
    },
}

# ---------------------------------------------------------------------------
# Database connection reuse. CONN_MAX_AGE keeps a connection alive across
# requests within a single Gunicorn worker instead of reconnecting every
# time — real pooling (pgbouncer) is a further upgrade once traffic
# justifies it, not built blind here (see docs/DEPLOYMENT.md).
# ---------------------------------------------------------------------------
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["connect_timeout"] = env.int("DB_CONNECT_TIMEOUT", default=10)

# ---------------------------------------------------------------------------
# Celery production tuning. Queue routing itself (default vs. emails) lives
# in config/celery.py's task_routes, since it's app wiring, not an
# environment-specific value — these three are genuinely
# environment-specific (a dev worker running CELERY_TASK_ALWAYS_EAGER
# never touches them).
# ---------------------------------------------------------------------------
CELERY_WORKER_PREFETCH_MULTIPLIER = env.int("CELERY_WORKER_PREFETCH_MULTIPLIER", default=4)
CELERY_TASK_ACKS_LATE = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# ---------------------------------------------------------------------------
# Logging. Console output goes to stdout/stderr, captured by journald under
# the gunicorn/celery-worker/celery-beat systemd units (`journalctl -u
# gunicorn`, etc. — see docs/MONITORING.md and docs/TROUBLESHOOTING.md).
# Rotating file handlers back that up on disk for anything that outlives
# journald's retention window or needs grepping directly. Uncaught
# exceptions in a request are additionally emailed to admins via Django's
# built-in AdminEmailHandler, reusing the EMAIL_* settings already
# configured in base.py — no separate error-tracking service wired in yet.
# ---------------------------------------------------------------------------
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

ADMINS = [tuple(pair.split(":", 1)) for pair in env.list("DJANGO_ADMINS", default=[]) if ":" in pair]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "django_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "django.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 10,
            "formatter": "verbose",
        },
        "security_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "security.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 10,
            "formatter": "verbose",
        },
        "mail_admins": {
            "level": "ERROR",
            "class": "django.utils.log.AdminEmailHandler",
        },
    },
    "root": {
        "handlers": ["console", "django_file"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console", "django_file", "mail_admins"],
            "level": "INFO",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console", "security_file", "mail_admins"],
            "level": "WARNING",
            "propagate": False,
        },
        "celery": {
            "handlers": ["console", "django_file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
