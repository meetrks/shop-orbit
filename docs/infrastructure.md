# Infrastructure

This document covers the production-grade backend/frontend stack:
PostgreSQL, Celery/Redis, Django REST Framework, and the Tailwind/HTMX/
Alpine.js frontend build. For the payment gateway architecture, see
`docs/payments.md`.

## Stack

- **Database**: PostgreSQL (via `psycopg[binary]`, Django's psycopg3 driver).
- **Background jobs**: Celery, broker + (no) result backend on Redis.
- **API layer**: Django REST Framework — installed with secure defaults, no
  endpoints yet. Added only where a feature genuinely needs a JSON API;
  most dynamic UI is HTMX partials instead.
- **CSS**: Tailwind v4, compiled via the Tailwind CLI (no bundler, no
  PostCSS config needed — v4 handles that internally).
- **Interactivity**: HTMX + Alpine.js, vendored as static files (not
  CDN-loaded) for production reliability and to avoid an external
  dependency at every page load.

## Local setup

### 1. PostgreSQL + Redis

Both run as Homebrew services:

```sh
brew services start postgresql@15
brew services start redis
```

Create the database and a dedicated (non-superuser) role once:

```sh
psql -d postgres -c "CREATE ROLE shoporbit WITH LOGIN PASSWORD 'change-me' CREATEDB;"
psql -d postgres -c "CREATE DATABASE shoporbit OWNER shoporbit ENCODING 'UTF8';"
```

(`CREATEDB` is required so Django's test runner can create/destroy the
ephemeral test database — see `manage.py test`.)

Set `DATABASE_URL` in `.env` accordingly (see `.env.example`):

```
DATABASE_URL=postgres://shoporbit:change-me@localhost:5432/shoporbit
```

### 2. Frontend build (Tailwind + HTMX + Alpine)

```sh
npm install
npm run build   # one-off compile: static/css/tailwind.css + vendored JS
# or, during active frontend work:
npm run watch   # recompiles static/css/tailwind.css on every save
```

`static/css/tailwind_src.css` is the tracked source (imports Tailwind +
the project's one custom color, `navy`); `static/css/tailwind.css` is the
generated build artifact (gitignored — always rebuild, never hand-edit).
`static/js/vendor/htmx.min.js` and `alpine.min.js` are committed directly
(small, version-pinned, don't need rebuilding when templates change).

**Deploying**: run `npm run build` before `collectstatic` — the compiled
CSS/vendored JS must exist on disk for `collectstatic` to pick them up.

### 3. Celery worker (+ beat, for scheduled tasks)

```sh
uv run celery -A config worker -l info
```

Requires Redis running (`CELERY_BROKER_URL`, default
`redis://localhost:6379/0`). Tasks:

- `common.tasks.send_email_task` — every notification email in the
  project funnels through `common.emails.send_templated_email`, which
  renders the email synchronously (so a broken template fails loudly in
  the request that triggered it) and dispatches only the final send as a
  Celery task (retried up to 3 times on failure).
- `inventory.tasks.release_expired_reservations` — a **periodic** task
  (`CELERY_BEAT_SCHEDULE`, every 5 minutes) that releases stock held by
  checkouts nobody ever finished (`RESERVATION_TIMEOUT_MINUTES`, default
  30) and marks the order `EXPIRED`. This needs Celery **beat** running
  somewhere, not just a worker:
  ```sh
  # local dev: one process handles both
  uv run celery -A config worker -B -l info
  # production: separate worker and beat processes
  uv run celery -A config worker -l info
  uv run celery -A config beat -l info
  ```
  Uses a plain settings-dict schedule (no `django-celery-beat`) since
  there's only one fixed-interval job — reach for that package if a
  future task needs an admin-editable schedule.

**Tests never need a running worker, beat, or Redis**:
`CELERY_TASK_ALWAYS_EAGER` defaults to `True` whenever `manage.py test` is
the entry point (checked via `"test" in sys.argv` in
`config/settings/base.py`), so tasks run synchronously in-process during
the test suite.

### 4. Run the app

```sh
uv run python manage.py runserver
```

## Adding a new Celery task

Put it in the relevant app's `tasks.py` (Celery's `autodiscover_tasks()`
picks up any `tasks.py` inside an app listed in `INSTALLED_APPS`
automatically). `common` is a plain Python package, not a registered app
(see `common/models.py`'s docstring) — `common.tasks` is imported
directly from `common.emails` instead of relying on autodiscovery; follow
that pattern if you add another task there.

## Adding a new DRF endpoint

Only add one when a feature genuinely needs a JSON API (e.g. a future
mobile app, or a JS component that needs structured data HTMX can't
express as an HTML partial). `REST_FRAMEWORK` in `config/settings/base.py`
already sets a secure baseline (session auth, `IsAuthenticated` by
default, page-number pagination) — override per-view as needed, and wire
the app's endpoints under its own `urls.py` like every other app.

## Production deployment

Local setup above covers development only. For deploying to a real
server, see `docs/DEPLOYMENT.md` (the full walkthrough) and
`docs/ENVIRONMENT_VARIABLES.md` (every env var, dev and production). This
file's local-setup instructions still apply as-is when working on the
code — production just adds a settings module
(`config/settings/production.py`), Gunicorn/Nginx/systemd, and the
supporting scripts under `scripts/`.
