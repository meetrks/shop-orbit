# Server Setup

What `scripts/install_server.sh` does, step by step, and why — read this
before running it as root on a real server. See `docs/DEPLOYMENT.md` for
the full deployment walkthrough this script is step 3 of.

## What it installs

- **Python 3.11** + venv/pip — the app's runtime.
- **PostgreSQL** + `postgresql-contrib` — the database. Enabled as a
  systemd service (`systemctl enable --now postgresql`); no custom unit
  needed, Ubuntu's own package provides one.
- **Redis** — Celery broker + Django cache backend. Same story:
  Ubuntu's package-provided systemd unit, just enabled.
- **Nginx** — reverse proxy + static/media file server + TLS
  termination.
- **Certbot** (+ `python3-certbot-nginx`) — Let's Encrypt certificate
  issuance/renewal, with Nginx integration so it can edit the site config
  to add HTTPS automatically.
- **git, curl, rsync** — used by `deploy.sh`.
- **UFW, Fail2ban** — firewall and brute-force protection (see
  `docs/SECURITY.md`).
- **Node.js/npm** — required to run `npm run build` (the Tailwind CSS
  compile step) during every deploy.
- **build-essential, libpq-dev** — needed to build a couple of Python
  packages' native extensions during `uv sync` (notably `psycopg` if it
  ever needs to compile from source instead of using its binary wheel).
- **uv** — installed via its official installer script if not already
  present, symlinked into `/usr/local/bin` so every user can find it.

## What it creates

- A dedicated **`deploy` system user** (`--system --create-home --shell
  /usr/sbin/nologin`) — everything runs as this user, never as root.
  `nologin` means nobody can actually SSH in or get an interactive shell
  as `deploy`; it exists purely to own and run the app's processes.
- **`/srv/shoporbit/`** — the release tree:
  - `releases/<timestamp>/` — one full checkout per deploy.
  - `shared/.env` — production secrets, outside the swapped release
    tree so they survive every deploy untouched.
  - `shared/media/` — uploaded files (product images, PicWeight jobs,
    invoices, packing slips) — also outside the release tree.
  - `shared/venv/` — the actual Python virtualenv, persistent across
    releases (rebuilding a fresh venv containing OpenCV/Pillow/etc. on
    every deploy would be slow; `uv sync` updates it in place instead).
  - `scripts/` — a persistent copy of `scripts/*.sh`, refreshed at the
    end of every successful `deploy.sh` run. This is the stable entry
    point `.github/workflows/deploy.yml` calls — see that file's
    comments for why it can't just call `current/scripts/deploy.sh`
    (chicken-and-egg problem before any release exists yet).
  - `backups/` — where `scripts/backup.sh` writes database/media
    backups.
  - `current` — a symlink to whichever `releases/<timestamp>/` is live.
    Never a real directory itself.

If `shared/.env` doesn't exist yet, the script seeds it from
`.env.example` and chmods it `600` — **you must still edit it with real
values** (the script prints a reminder).

## What it installs from this repo

- `deploy/systemd/{gunicorn,celery-worker,celery-beat}.service` →
  `/etc/systemd/system/`, then `daemon-reload` + `enable` (not `start` —
  there's no release to run yet at this point in the setup).
- `deploy/nginx/shoporbit.conf` → `/etc/nginx/sites-available/`,
  symlinked into `sites-enabled/`, replacing Nginx's default site.
  `nginx -t` is run afterward — it's **expected to fail** the very first
  time, since the config references a TLS certificate that doesn't exist
  until Certbot runs (see `docs/DEPLOYMENT.md` step 7). The script warns
  about this rather than treating it as a fatal error.

## Security hardening applied

- **UFW**: allows SSH (`ufw allow OpenSSH`), 80, 443; denies everything
  else; then enabled. If you've moved SSH off port 22, adjust the
  `OpenSSH` app profile or add an explicit port rule before running this
  script, or you'll lock yourself out.
- **Fail2ban**: an `sshd` jail (1-hour ban after 5 failed attempts within
  10 minutes), written to `/etc/fail2ban/jail.local` so it survives a
  `fail2ban-common.conf` package update.

See `docs/SECURITY.md` for the full rationale and what's deliberately
*not* covered here (e.g. changing the SSH port, disabling password auth
entirely — both worth doing, both outside what a repo-tracked script
should silently do to your server's SSH access without you explicitly
choosing to).

## Idempotency

Every step is safe to re-run: `apt-get install -y` no-ops on already-
installed packages, user/directory creation checks existence first,
`.env` is only seeded if absent, systemd/Nginx configs are simply
overwritten with the repo's current version, and UFW/Fail2ban rules are
declarative (re-applying the same rule is a no-op). Re-run this script
after pulling a repo update that changed `deploy/systemd/*` or
`deploy/nginx/*`, then `sudo systemctl daemon-reload` if the systemd
units themselves changed.
