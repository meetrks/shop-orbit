# Restore

Disaster-recovery procedure using backups produced by `scripts/backup.sh`
(see `docs/BACKUP.md`). Destructive — read this whole page before running
anything against production.

## Before you start

1. **Stop traffic reaching the app** so nothing writes to the database
   mid-restore: `sudo systemctl stop gunicorn celery-worker celery-beat`.
2. **Know which backup you're restoring** — list what's available:
   ```sh
   ls -la /srv/shoporbit/backups/
   ```
3. If you're not sure this is the right call, `scripts/backup.sh` first
   to snapshot the current (possibly broken) state before overwriting
   it — a bad restore shouldn't also destroy the evidence of what went
   wrong.

## Restore

```sh
sudo -u deploy bash /srv/shoporbit/scripts/restore.sh \
    /srv/shoporbit/backups/db_<timestamp>.dump.gz \
    /srv/shoporbit/backups/media_<timestamp>.tar.gz
```

Without `--yes`, this prompts for confirmation before touching anything
(type `yes` to proceed). The media argument is optional — omit it to
restore only the database.

What it does:
1. `pg_restore --clean --if-exists` — drops and recreates every object in
   the dump, so the database ends up in exactly the dumped state (not a
   merge with whatever was there before).
2. Extracts the media tarball over `shared/media/` (if given).
3. Runs a trivial `SELECT 1` against the restored database to confirm
   it's actually reachable before declaring success.

## After restoring

```sh
sudo systemctl start gunicorn celery-worker celery-beat
bash /srv/shoporbit/scripts/health_check.sh
```

Check the app in a browser, confirm recent orders/products look right
for the point-in-time you restored to, then resume normal operations.

## If the restore itself fails

Don't guess — `pg_restore`'s own output tells you what broke (a
version mismatch between the `pg_dump` that created the file and the
`pg_restore` restoring it is the most common cause; both come from the
same PostgreSQL major version installed by `scripts/install_server.sh`,
so this only bites if you're restoring a very old backup onto an
upgraded server). See `docs/TROUBLESHOOTING.md`.
