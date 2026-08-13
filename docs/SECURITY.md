# Security

What's hardened, why, and what's deliberately left as a documented
follow-up rather than silently faked.

## Transport security (`config/settings/production.py`)

- `SECURE_SSL_REDIRECT=True` — every HTTP request is redirected to
  HTTPS.
- `SESSION_COOKIE_SECURE=True` / `CSRF_COOKIE_SECURE=True` — cookies
  never sent over plain HTTP.
- `SECURE_PROXY_SSL_HEADER` trusts Nginx's `X-Forwarded-Proto` header to
  know a request was originally HTTPS even though Nginx proxies to
  Gunicorn over plain HTTP internally. This is only safe because Nginx
  is the *only* thing that can reach Gunicorn's Unix socket — a client
  can't set this header directly and have Django believe it, since the
  request never reaches Django except via Nginx rewriting/adding it.
- `SECURE_BROWSER_XSS_FILTER`, `SECURE_CONTENT_TYPE_NOSNIFF`,
  `X_FRAME_OPTIONS="DENY"`, `SECURE_REFERRER_POLICY="same-origin"` —
  standard Django hardening headers.
- **HSTS rollout**: `SECURE_HSTS_SECONDS` defaults to a full year
  (31536000), but consider starting lower (e.g. `300`) for the first
  deploy via `DJANGO_SECURE_HSTS_SECONDS` — HSTS tells browsers to
  *refuse* plain HTTP to this domain for that long, so a broken
  cert/config becomes unreachable, not just insecure, until the max-age
  expires. Raise it once you're confident HTTPS is solid.
  `SECURE_HSTS_PRELOAD` defaults to `False` deliberately — submitting to
  the browser preload list is close to irreversible (removal takes
  months and requires shipping updates to every major browser), so it's
  an explicit opt-in, not a default.

## Firewall & intrusion prevention (`scripts/install_server.sh`)

- **UFW**: default-deny, explicit allow for SSH/80/443 only.
- **Fail2ban**: an `sshd` jail bans an IP for an hour after 5 failed
  login attempts in 10 minutes.

### Deliberately not automated

- **Changing the SSH port / disabling password auth entirely** — both
  genuinely worth doing (`PasswordAuthentication no` + key-only auth in
  `/etc/ssh/sshd_config`, plus optionally moving off port 22), but a
  repo-tracked script silently rewriting your SSH access on a server it
  doesn't have interactive access to is exactly the kind of change that
  can lock you out permanently if something's misconfigured. Do this
  manually, verify a *second* SSH session still connects before closing
  your first one, then restart `sshd`.
- **Disabling root login** — same reasoning; do it manually, verify the
  `deploy` user (or your own sudo-capable user) can still get in first.

## Application-level protections (already in Django, unchanged by this initiative)

- CSRF: Django's `CsrfViewMiddleware`, already in `MIDDLEWARE` — every
  POST form in the app already includes `{% csrf_token %}`.
- SQL injection: the Django ORM parameterizes every query by
  construction; nothing in this codebase drops to raw SQL with
  string-interpolated user input.
- Clickjacking: `X_FRAME_OPTIONS="DENY"` (production) +
  `XFrameOptionsMiddleware` (all environments).
- Password storage: Django's default PBKDF2 hasher, plus the four
  standard `AUTH_PASSWORD_VALIDATORS` already configured in
  `config/settings/base.py`.

## Nginx security headers (`deploy/nginx/shoporbit.conf`)

`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` are set
here too (not just by Django) so they also apply to static files Nginx
serves directly, which never pass through Django's middleware.

**Content-Security-Policy is deliberately loose**
(`script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'`)
— HTMX and Alpine.js both rely on inline event-handler-style attributes
(`hx-*`, `x-data`, `x-on:click`, etc.), and a strict CSP without
`unsafe-inline` (or a nonce/hash scheme wired through every template)
would break them outright. Tightening this to a nonce-based policy is
real, non-trivial follow-up work — not done here, and not worth faking
with a CSP that looks strict but is actually full of holes.

## Secret rotation

- **`DJANGO_SECRET_KEY`**: rotating it invalidates every existing
  session and any signed data (e.g. password-reset tokens) currently in
  flight. Plan a rotation for low-traffic hours; there's no zero-downtime
  way to rotate this one.
- **Razorpay keys**: rotate from the Razorpay dashboard, update
  `shared/.env`, redeploy (or just `systemctl restart gunicorn
  celery-worker celery-beat` after editing `.env` directly — a full
  `deploy.sh` run isn't required for a secrets-only change).
- **Database password**: `ALTER ROLE shoporbit WITH PASSWORD
  'new-password';` in psql, update `DATABASE_URL` in `shared/.env`,
  restart services.
- **Never commit `shared/.env`** — it's created once by
  `install_server.sh` (seeded from `.env.example`, then hand-edited) and
  lives only on the server and in `scripts/backup.sh`'s scope
  indirectly (it isn't backed up by `backup.sh` today — back it up
  separately/manually if you rely on it as your only copy of production
  secrets).

## Security scanning (currently via `pre-commit`, not CI — see `docs/CI_CD.md`)

- **Bandit** — static analysis for common Python security anti-patterns,
  run at `--severity-level medium` (fails the build on medium+ findings).
  One finding was triaged during this initiative: `gunicorn.conf.py`'s
  use of `/dev/shm` as `worker_tmp_dir` is flagged as
  "hardcoded_tmp_directory" (B108) — this is intentional (see that
  file's comments) and suppressed inline with `# nosec B108` plus a
  reason.
- **pip-audit** — checks locked dependency versions against known
  vulnerability databases. Clean as of this writing; re-run
  `scripts/update_dependencies.sh` periodically so it stays that way.
