# Contributing

Thanks for considering a contribution — this guide covers everything you
need to get set up and send a pull request.

## Getting set up

Full local setup (PostgreSQL, Redis, Celery, the Tailwind build) is covered
in [`docs/infrastructure.md`](docs/infrastructure.md). The short version:

```sh
# Python deps (this project uses uv, not pip/poetry)
uv sync --all-groups

# Frontend build (Tailwind CSS + vendored HTMX/Alpine)
npm install
npm run build

# Environment
cp .env.example .env
# edit .env: at minimum, set DJANGO_SECRET_KEY and DATABASE_URL

# Database
uv run python manage.py migrate
uv run python manage.py createsuperuser

# Run it
uv run python manage.py runserver
```

Run the test suite with:

```sh
uv run python manage.py test
```

## Before you send a PR

1. **Install the git hooks** (one-time, see below) so lint/format/security
   checks run automatically.
2. **Write tests** for behavior changes — this codebase has close to 100%
   coverage on business logic, and PRs are expected to keep it that way.
3. **Run the full test suite** — `uv run python manage.py test`.
4. **Keep commits focused.** One logical change per commit/PR is much easier
   to review than a large mixed diff.

## Git hooks

This repo uses [`pre-commit`](https://pre-commit.com/) for local checks —
the same tools (`ruff`, `bandit`) that CI runs, catching issues before you
even push.

```sh
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

- **pre-commit** (on every commit): `ruff check --fix` and `ruff format`.
- **pre-push** (only when pushing a branch that resolves to `main`): the
  full test suite, so a broken build never reaches `main`.

You can also run everything manually at any time:

```sh
uv run pre-commit run --all-files
```

## Code style

- **Linting/formatting**: [`ruff`](https://docs.astral.sh/ruff/) — config
  lives in `pyproject.toml`. No Black/Pylint/isort — ruff replaces all
  three.
- **Security**: [`bandit`](https://bandit.readthedocs.io/) runs in CI and
  pre-commit.
- Match the surrounding code's conventions (service-layer functions in
  `services.py`, not business logic in views; explicit over clever; comments
  explain *why*, not *what*).

## Project structure

Each Django app owns one bounded concern — see each app's top-level
docstring (`accounts/models.py`, `cart/models.py`, `payments/services.py`,
etc.) for what it's responsible for and how it talks to the others. Start
there before adding code to an app you haven't touched yet.

## Reporting bugs / requesting features

Please use the issue templates — they ask for exactly what's needed to
triage quickly (repro steps for bugs, motivation/use case for features).

## Reporting security issues

**Do not** open a public issue for a security vulnerability — see
[`SECURITY.md`](SECURITY.md) for how to report privately.

## Code of Conduct

This project follows a [Code of Conduct](CODE_OF_CONDUCT.md). By
participating, you're expected to uphold it.
