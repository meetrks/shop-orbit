"""
Gunicorn configuration for production (see deploy/systemd/gunicorn.service,
which invokes `gunicorn -c gunicorn.conf.py config.wsgi:application`).

Not used in local development — `manage.py runserver` is what you want
there. This file is only ever loaded when explicitly passed via `-c`.
"""

import multiprocessing
import os

# Binds to a Unix socket, not a TCP port — Nginx is the only thing that
# talks to Gunicorn directly (see deploy/nginx/shoporbit.conf), so
# nothing else on the box (or the internet) can reach it. The directory
# must exist and be writable by the user Gunicorn runs as — see
# deploy/systemd/gunicorn.service's RuntimeDirectory= and
# scripts/install_server.sh.
bind = f"unix:{os.environ.get('GUNICORN_SOCKET', '/run/shoporbit/gunicorn.sock')}"

# Overridable for a known-size VPS via GUNICORN_WORKERS; the classic
# (2 * cpu_count) + 1 formula otherwise.
workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
threads = int(os.environ.get("GUNICORN_THREADS", 2))
worker_class = "gthread"

timeout = int(os.environ.get("GUNICORN_TIMEOUT", 30))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", 30))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", 5))

# Recycles each worker after this many requests (+/- jitter, so workers
# don't all recycle in lockstep) — bounds gradual memory growth from any
# leak rather than requiring a manual restart to recover from one.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", 1000))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", 50))

# /dev/shm (tmpfs) rather than the default /tmp — avoids a well-known
# Gunicorn+systemd issue where a slow or full disk under /tmp delays the
# worker heartbeat enough that the master thinks a healthy worker has
# hung and kills it. Overridable since /dev/shm doesn't exist on macOS —
# only relevant for a local `gunicorn -c gunicorn.conf.py` smoke test.
worker_tmp_dir = os.environ.get("GUNICORN_WORKER_TMP_DIR", "/dev/shm")  # nosec B108 - intentional, see comment above

# Logs to stdout/stderr; systemd/journald captures them (see
# `journalctl -u gunicorn`, docs/MONITORING.md, docs/TROUBLESHOOTING.md) —
# no separate log file to manage/rotate here.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
