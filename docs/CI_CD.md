# CI/CD

> **Currently removed.** `.github/workflows/ci.yml` was pulled for now —
> this doc describes the pipeline as it existed and is the reference for
> reinstating it. The local `pre-commit` hooks (see `CONTRIBUTING.md`)
> still run the same lint/format/security checks on every commit/push in
> the meantime. `.github/workflows/deploy.yml` still exists but is
> currently inert without CI to trigger it.

## `.github/workflows/ci.yml`

Runs on every push and every PR into `main`:

1. Checkout, Python 3.11, `uv sync --all-groups --frozen`.
2. Postgres + Redis as GitHub Actions **service containers** — an
   ephemeral test database/broker for this job only. This is unrelated
   to the no-Docker native-systemd production deployment described in
   `docs/DEPLOYMENT.md`; it's purely a CI convenience for spinning up
   throwaway service dependencies.
3. `ruff check .` — lint (real bugs: unused imports, undefined names,
   etc.) — a hard gate.
4. `ruff format --check .` — hard gate.
5. `bandit -r .` — security static analysis, medium+ severity, hard
   gate. One pre-existing finding (`gunicorn.conf.py`'s use of
   `/dev/shm`) is suppressed inline with `# nosec B108` and a reason —
   see `docs/SECURITY.md`.
6. `pip-audit` against the locked dependency versions — hard gate.
   Currently clean; if a future finding needs triaging before a fix is
   available upstream, that's the moment to add
   `continue-on-error: true` back with a comment explaining why,
   mirroring how step 4 is handled.
7. `manage.py test` — the full suite (240 tests as of this writing).
8. `npm ci && npm run build` + `manage.py collectstatic --noinput` — a
   build-sanity check, catching a broken Tailwind build or missing
   static asset before it ever reaches a real deploy.

## `.github/workflows/deploy.yml`

Triggered by `workflow_run` on `ci.yml` completing successfully, filtered
to the `main` branch — so a deploy can only ever follow a green CI run
on `main`, never an untested branch or a failed run.

Requires three repository secrets to do anything:

- `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` — SSH connection details (see
  `appleboy/ssh-action` in the workflow file).
- `GIT_REPO_URL` — passed through to `scripts/deploy.sh` on the server.

**Until those secrets exist, this workflow is syntactically correct and
will fire on every CI success, but the SSH step will simply fail** (no
connection details to use) — that's expected, not a bug, for a project
with no VPS provisioned yet. Add the secrets once `docs/DEPLOYMENT.md`'s
steps 1–3 are done (server exists, `install_server.sh` has run).

The actual deploy logic (clone, build, migrate, collectstatic, symlink
switch, restart, health-check, auto-rollback) all lives in
`scripts/deploy.sh` on the server — this workflow's only job is "SSH in
and run that script." Nothing about the deploy process is duplicated
between the two.

## Adding a new CI check

Add a step to `ci.yml`. If it's the kind of check that might have
pre-existing findings needing triage before it can be a hard gate
(hypothetically, a `pip-audit` finding with no fix yet available), add
`continue-on-error: true` with a comment explaining exactly what needs to
happen before it becomes a hard gate — don't leave a soft-failing check
without an explanation for why it's soft, or it'll quietly stay soft
forever.

## Rotating the CI/CD secrets themselves

If `VPS_SSH_KEY` needs rotating (e.g. the deploy user's key was
compromised, or you're moving to a new server): generate a new keypair,
add the public half to the `deploy` user's `~/.ssh/authorized_keys` on
the server, update the `VPS_SSH_KEY` secret in GitHub with the new
private half, then remove the old public key from the server.
