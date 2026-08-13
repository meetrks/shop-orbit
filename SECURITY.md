# Security Policy

## Reporting a Vulnerability

If you find a security vulnerability in this project, please **do not** open
a public issue. Instead, report it privately via
[GitHub Security Advisories](../../security/advisories/new) for this
repository.

Please include, where possible:

- A description of the vulnerability and its potential impact
- Steps to reproduce it
- Any relevant logs, screenshots, or proof-of-concept code

We'll acknowledge your report as soon as possible and keep you updated as we
investigate and address the issue. Once a fix is available, we'll coordinate
on disclosure timing with you.

## Supported Versions

This project doesn't yet follow a formal versioned release process — the
`main` branch is the supported version. Security fixes land there first.

## Automated Scanning

Local `pre-commit` hooks (see [`CONTRIBUTING.md`](CONTRIBUTING.md)) run on
every commit/push:

- [`ruff`](https://docs.astral.sh/ruff/) — linting
- [`bandit`](https://bandit.readthedocs.io/) — static security analysis

[`pip-audit`](https://pypi.org/project/pip-audit/) (known-vulnerability
scanning of dependencies) is available via `uv run pip-audit` but not yet
wired into a hook. GitHub Actions CI previously ran all of these on every
push/PR — see [`docs/CI_CD.md`](docs/CI_CD.md), currently removed pending
reinstatement.

## Hardening Reference

For a detailed look at what's hardened in this codebase and why (transport
security, firewall/intrusion prevention, webhook signature verification,
secrets handling, and what's deliberately left as a documented follow-up
rather than silently faked), see [`docs/SECURITY.md`](docs/SECURITY.md).
