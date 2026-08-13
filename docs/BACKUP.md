# Backups

## What gets backed up

`scripts/backup.sh` produces two timestamped files per run:

- `db_<timestamp>.dump.gz` — `pg_dump --format=custom`, gzip'd. Custom
  format (not plain SQL) so `pg_restore` can do selective/parallel
  restores if ever needed, and so the dump is verified readable
  (`gunzip -t`) immediately after creation, before the run is considered
  successful.
- `media_<timestamp>.tar.gz` — a tarball of `shared/media/` (product
  images, PicWeight uploads, GST invoices, packing slips).

Both land in `$APP_DIR/backups` (default `/srv/shoporbit/backups`).

## What does NOT get backed up automatically

- **`shared/.env`** (production secrets) — deliberately not bundled into
  the routine backup rotation, since a backup file is exactly the kind
  of thing that might end up copied somewhere less secure than the
  server itself. Keep a separate, deliberately-secured copy (a password
  manager, a secrets vault) — see `docs/SECURITY.md`.
- **Static files** (`current/staticfiles/`) — regenerated on every
  deploy from source (`npm run build` + `collectstatic`), never
  hand-edited, so there's nothing unique to back up there.
- **Application code** — it's in git; the repo itself is the backup.

## Scheduling

Not wired to a cron/systemd timer automatically by this initiative —
add one explicitly once you're ready to rely on it running unattended,
e.g.:

```sh
# /etc/cron.d/shoporbit-backup
0 3 * * * deploy APP_DIR=/srv/shoporbit /srv/shoporbit/scripts/backup.sh >> /var/log/shoporbit-backup.log 2>&1
```

## Retention

`RETENTION_DAYS` (default 14) — files older than this are deleted at the
end of every `backup.sh` run. Raise it if you want a longer window, or
copy backups off-server (e.g. to S3/rsync to another host) before they
age out if you need longer-than-local retention — this script only
manages the local copies.

## Verifying a backup is actually restorable

Don't just trust that `backup.sh` ran without error — periodically
restore a backup into a scratch database and confirm the app boots
against it:

```sh
createdb shoporbit_restore_test
DATABASE_URL=postgres://shoporbit:change-me@localhost:5432/shoporbit_restore_test \
    bash scripts/restore.sh /srv/shoporbit/backups/db_<timestamp>.dump.gz --yes
dropdb shoporbit_restore_test
```

See `docs/RESTORE.md` for the real (production) restore procedure.
