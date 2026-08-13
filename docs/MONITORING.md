# Monitoring

## Health endpoints (`common/health.py`)

- **`GET /health/live/`** — the process is up, no dependency checks.
  Returns `{"status": "ok"}` unconditionally. Wire this into whatever
  restarts a hung process (systemd already does via
  `Restart=on-failure`; an external uptime monitor hitting this tells
  you "is the server reachable at all").
- **`GET /health/ready/`** — checks the database and cache are actually
  reachable. Returns `{"status": "ok", "database": "ok", "cache": "ok"}`
  with HTTP 200 when healthy, or the same shape with whichever check
  failed and HTTP 503 otherwise. This is what `scripts/deploy.sh` polls
  (via `scripts/health_check.sh`) to decide whether a deploy succeeded.

Both are unauthenticated by design — a load balancer or deploy script
can't log in first. Neither leaks anything sensitive (no stack traces,
no config values) even in the failure case.

**Celery is deliberately not checked over HTTP** — a `celery inspect
ping` round-trip is slow and only matters to deploy/monitoring tooling,
not to whether a page can load. `scripts/health_check.sh` checks it
separately via the CLI.

## `scripts/health_check.sh`

Run manually or from any external monitor that can SSH in / run a
script: checks both HTTP endpoints, then pings Celery workers via
`celery -A config inspect ping`. Exits non-zero if anything fails —
that's what `deploy.sh`/`rollback.sh` key off of, but it's equally
useful run by hand or from an external monitoring agent (cron + alert on
non-zero exit, or wired into whatever paging system you use).

## Logs

Everything logs to stdout/stderr, captured by systemd/journald:

```sh
journalctl -u gunicorn -f
journalctl -u celery-worker -f
journalctl -u celery-beat -f
journalctl -u nginx -f       # nginx logs to its own files by default, see below
```

Rotating file handlers additionally write to `logs/` inside the current
release (`logs/django.log`, `logs/security.log`) — see `LOGGING` in
`config/settings/production.py`. journald's own retention is usually
enough day-to-day; the file handlers exist for anything that outlives
journald's window or needs grepping directly without journalctl syntax.

Nginx's access/error logs are at `/var/log/nginx/shoporbit_access.log`
and `/var/log/nginx/shoporbit_error.log` (see
`deploy/nginx/shoporbit.conf`) — Ubuntu's `logrotate` already
manages `/var/log/nginx/*` by default, no extra setup needed there.

## System resources

Not wired into an automated exporter by this initiative (see
`docs/DEPLOYMENT.md`'s "deliberately not built" section re: Prometheus).
For now, check directly:

```sh
df -h /srv                 # disk
free -h                    # memory
top                        # CPU / per-process memory
systemctl status gunicorn celery-worker celery-beat postgresql redis-server nginx
```

## Uptime monitoring

Point any external uptime service (UptimeRobot, Better Uptime, a simple
cron+curl+alert setup, etc.) at `https://yourdomain.com/health/ready/`
and alert on anything other than HTTP 200. This is the single most
useful automated check to have running from day one, since it's the
only one that observes the app the way a real visitor would (from
outside the server, over the real network path, through Nginx and TLS).

## Error monitoring (Sentry)

Off by default — `SENTRY_DSN` is blank until you set it, so a fresh
checkout never reports anywhere. To turn it on:

1. Create a project at [sentry.io](https://sentry.io) (or point at a
   self-hosted instance) and copy its DSN.
2. Set `SENTRY_DSN` in production's environment (see
   `deploy/systemd/*.service`'s `EnvironmentFile=`). Optionally set
   `SENTRY_ENVIRONMENT` (defaults to `production`/`development` based on
   `DEBUG`) and `SENTRY_TRACES_SAMPLE_RATE` (performance tracing; stays 0
   — errors only — unless you deliberately turn it on).
3. Restart `gunicorn`, `celery-worker`, and `celery-beat` so the new
   environment variable takes effect.

Once set, this covers three things automatically, with no further code
changes:

- **Django** — uncaught exceptions during a request.
- **Celery** — task failures (`release_expired_reservations`, outbound
  email sending, etc.).
- **Everything already logged at WARNING or above** — including the
  payment/webhook signature-rejection and processing-failure paths in
  `payments/services.py` and `payments/views.py`, which already call
  `logger.warning`/`logger.exception` — via `LoggingIntegration`.

**Verify alerts actually arrive**: trigger a real event once Sentry is
configured (e.g. temporarily raise an exception in a throwaway view, or
use `python manage.py shell -c "1/0"` under `sentry_sdk`) and confirm it
shows up in the Sentry project and any configured alert notification
(email/Slack) actually fires — configuring the DSN alone doesn't
guarantee the notification channel on Sentry's side is wired up.
