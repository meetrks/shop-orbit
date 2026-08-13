# Shop Orbit

[![CI](https://github.com/meetrks/shop-orbit/actions/workflows/ci.yml/badge.svg)](https://github.com/meetrks/shop-orbit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Django 5](https://img.shields.io/badge/django-5.2-0C4B33.svg)](pyproject.toml)

A production-grade, server-rendered Django e-commerce storefront — catalog,
cart, Razorpay checkout, order fulfillment, and a staff operations
dashboard, built with a deliberately boring, maintainable stack: Django +
PostgreSQL + HTMX/Alpine.js, no SPA framework.

This isn't a toy demo. It's a real storefront's codebase, open-sourced as a
reference for building production Django e-commerce: service-layer
architecture, a real payment-gateway integration with webhook
reconciliation, background jobs, GST-compliant invoicing, and deployment
scripts for a plain VPS (no Docker/Kubernetes required).

## Features

**Storefront**
- Department → Category → Subcategory catalog with SEO-friendly URLs
- Product variants, image galleries, full-text + fuzzy search
- Cart, wishlist, coupons, an automatic "buy more, save more" quantity
  discount
- Customer reviews with verified-purchase badges
- Admin-configurable homepage sections — product shelves, a testimonials
  carousel, "Shop by Category," "Shop by Price" — no template edits needed
- PWA support (installable, offline-safe service worker)

**Checkout & payments**
- [Razorpay](https://razorpay.com/) integration (UPI, cards, netbanking,
  wallets) via a gateway-agnostic abstraction — swap providers without
  touching business logic
- Webhook-driven payment confirmation with idempotent reconciliation
- Automatic stock reservation during checkout, released on
  abandonment/failure
- Staff-initiated full/partial refunds from the dashboard

**Fulfillment & operations**
- GST-compliant tax invoice and packing-slip PDF generation
- Courier integration ([Delhivery](https://www.delhivery.com/)) for
  waybill creation and tracking sync
- Post-delivery returns workflow
- Inventory ledger: stock movements, suppliers, purchase orders
- A staff dashboard (store stats, order management, refunds, profitability
  report) separate from Django Admin
- **PicWeight**: an in-house tool that standardizes raw supplier photos
  into consistent, listing-ready product images

**Engineering**
- Service-layer architecture — business logic lives in `services.py`, not
  views or models
- Celery + Redis for background jobs (emails, stock-reservation expiry,
  stale-order cleanup)
- Extensive automated test coverage on business logic
- CI: lint (`ruff`), format check, security scan (`bandit`), dependency
  vulnerability scan (`pip-audit`), full test suite, frontend build
- Deployment scripts for a single VPS via systemd + Nginx — no
  Docker/Kubernetes required

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Django 5, Django REST Framework |
| Database | PostgreSQL |
| Background jobs | Celery + Redis |
| Frontend | Server-rendered Django templates, HTMX, Alpine.js |
| CSS | Tailwind CSS v4 |
| Payments | Razorpay |
| Courier | Delhivery |
| Package management | [`uv`](https://docs.astral.sh/uv/) (Python), npm (frontend build only) |
| Deployment | Gunicorn + Nginx + systemd (no containers) |

## Quick start

```sh
# Python dependencies
uv sync --all-groups

# Frontend build (Tailwind CSS + vendored HTMX/Alpine)
npm install
npm run build

# Environment
cp .env.example .env
# edit .env — at minimum, set DJANGO_SECRET_KEY and DATABASE_URL

# Database
uv run python manage.py migrate
uv run python manage.py createsuperuser

# Run it
uv run python manage.py runserver
```

Full local setup (PostgreSQL/Redis installation, Celery, every environment
variable) is in [`docs/infrastructure.md`](docs/infrastructure.md) and
[`docs/ENVIRONMENT_VARIABLES.md`](docs/ENVIRONMENT_VARIABLES.md).

## Running tests

```sh
uv run python manage.py test
```

## Documentation

| Doc | Covers |
|---|---|
| [`docs/infrastructure.md`](docs/infrastructure.md) | Stack overview, local setup |
| [`docs/ENVIRONMENT_VARIABLES.md`](docs/ENVIRONMENT_VARIABLES.md) | Every env var, dev and production |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Full production deployment walkthrough |
| [`docs/SERVER_SETUP.md`](docs/SERVER_SETUP.md) | Provisioning a fresh VPS |
| [`docs/CI_CD.md`](docs/CI_CD.md) | CI/CD pipeline |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Hardening details |
| [`docs/MONITORING.md`](docs/MONITORING.md) | Sentry, health checks, logging |
| [`docs/BACKUP.md`](docs/BACKUP.md) / [`docs/RESTORE.md`](docs/RESTORE.md) | Database backup/restore |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Common issues |
| [`docs/payments.md`](docs/payments.md) | Payment gateway architecture |
| [`docs/SHIPPING.md`](docs/SHIPPING.md) | Fulfillment/courier flow |

## Project structure

Each Django app owns one bounded concern:

```
accounts/    Email-based auth, addresses, staff store dashboard
catalog/     Product taxonomy, products, variants, reviews, search
cart/        Cart, checkout, orders, coupons
payments/    Gateway-agnostic payment/refund records + Razorpay integration
fulfillment/ Invoices, packing slips, shipment tracking, courier integration
inventory/   Stock ledger, suppliers, purchase orders
returns/     Post-delivery return requests
pages/       Static pages, contact form, homepage builder
picweight/   Supplier-photo-to-listing-image standardization tool
common/      Shared abstract models, email helper, background tasks
config/      Django settings, URL routing, Celery app
```

## Contributing

Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md) for
local setup, git hooks, and code style.

## Security

Please don't open a public issue for a security vulnerability — see
[`SECURITY.md`](SECURITY.md) for how to report one privately.

## License

MIT — see [`LICENSE`](LICENSE).
