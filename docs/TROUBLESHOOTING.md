# Troubleshooting

Common failure modes for this deployment, and the first commands to run
for each.

## The site returns a 502/503/504 from Nginx

Gunicorn isn't responding on its socket. Check:

```sh
sudo systemctl status gunicorn
sudo journalctl -u gunicorn -n 100 --no-pager
ls -la /run/shoporbit/gunicorn.sock   # should exist and be a socket, not missing
```

Common causes: Gunicorn crashed on startup (a settings/import error —
the journal will show a traceback), or the socket path in
`gunicorn.conf.py` (`GUNICORN_SOCKET`) doesn't match what Nginx's
`proxy_pass` in `deploy/nginx/shoporbit.conf` points at.

## `systemctl start gunicorn` fails immediately

```sh
sudo journalctl -u gunicorn -n 50 --no-pager
```

Almost always either: `DJANGO_SETTINGS_MODULE` not resolving (check
`deploy/systemd/gunicorn.service`'s `Environment=` line matches
`config.settings.production`), a missing/misconfigured `.env` value
(`DJANGO_SECRET_KEY` in particular — Django raises immediately if it's
unset), or the venv path (`/srv/shoporbit/shared/venv/bin/gunicorn`)
not existing yet because `deploy.sh` hasn't run successfully yet.

## `nginx -t` fails

```sh
sudo nginx -t
```

If the error mentions a missing certificate file
(`/etc/letsencrypt/live/.../fullchain.pem`), that's expected before the
first `scripts/renew_ssl.sh --issue` run — see `docs/DEPLOYMENT.md` step
7. Any other syntax error means `deploy/nginx/shoporbit.conf` was
hand-edited incorrectly; `nginx -t` always tells you the exact line.

## Celery tasks aren't running (emails not sending, reservations not expiring)

```sh
sudo systemctl status celery-worker celery-beat
sudo journalctl -u celery-worker -n 100 --no-pager
cd /srv/shoporbit/current && sudo -u deploy DJANGO_SETTINGS_MODULE=config.settings.production \
    /srv/shoporbit/shared/venv/bin/celery -A config inspect active
```

Check Redis is actually reachable (`redis-cli ping` should return
`PONG`), and that `CELERY_BROKER_URL` in `shared/.env` points at it
correctly. If the worker is up but a specific task never fires, confirm
it's routed to a queue the worker actually consumes — see
`config/celery.py`'s `task_routes` and
`deploy/systemd/celery-worker.service`'s `-Q default,emails` flag; a
task routed to a queue nothing consumes will sit in Redis forever
without erroring.

## A deploy failed and auto-rolled-back

`deploy.sh`'s own output says exactly where it stopped (clone, `uv
sync`, `npm run build`, migrations, collectstatic, or the post-restart
health check) — that's the step to re-run manually to see the actual
error, since the automatic rollback already reverted `current` for you.
Common culprits: a migration that depends on a manual step (rare, but
check the migration's own docstring/comments if one exists), `npm ci`
failing because `package-lock.json` and the installed Node version
drifted, or a genuinely broken change that the health check correctly
caught — in which case, fix the code, don't fight the rollback.

## The database won't accept connections

```sh
sudo systemctl status postgresql
sudo -u postgres psql -c "SELECT 1;"
```

If PostgreSQL itself is fine but the app can't connect, check
`DATABASE_URL` in `shared/.env` — a stale/incorrect password after a
`ALTER ROLE ... PASSWORD` rotation (see `docs/SECURITY.md`) is the usual
cause.

## Health check keeps failing after what looks like a clean deploy

```sh
curl -v http://localhost/health/ready/     # from the server itself, bypassing DNS/TLS
```

If this returns `{"database": "error: ...", ...}` or similar, that
specific error message is the database/cache-connection failure — not a
generic "something's wrong," read it directly.

## Fail2ban banned an IP you needed (e.g. yourself)

```sh
sudo fail2ban-client status sshd
sudo fail2ban-client set sshd unbanip <your-ip>
```
