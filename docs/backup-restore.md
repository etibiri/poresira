<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Backup and restore

PoreSira keeps all of its state in PostgreSQL (sequencing file contents are never stored in
the database, only their path, backend, size and checksums; back those files up separately).

## What a backup contains

`scripts/backup.sh` runs `pg_dump` in custom format and writes two files to `BACKUP_DIR`:

- `poresira-<UTC timestamp>.dump`: the database, including the audit log and its hash chain.
- `poresira-<UTC timestamp>.dump.sha256`: its checksum.

Access privileges are part of the dump on purpose: they carry the rule that the application
role cannot modify audit tables. Object ownership is not (`--no-owner`).

Files older than `BACKUP_KEEP_DAYS` (default 30) are removed after each backup.

## Making backups

| Profile | How |
|---|---|
| `labo`, `plateforme` | The `backup` service dumps every `BACKUP_INTERVAL_HOURS` (default 24). |
| any, including `poste` | `docker compose run --rm backup /scripts/backup.sh` |

Set `BACKUP_DIR` to a folder on another disk or on the NAS; a backup stored next to the
database does not survive a disk failure. Set `BACKUP_OWNER` to your `uid:gid` so the files
belong to you on the host.

After each backup, note the head hash printed by
`docker compose exec web python manage.py verify_audit_chain` somewhere outside the server.
It lets you detect later truncation of the audit log, which the chain alone cannot reveal.

## Restoring

Restoring **replaces** the current database. From the repository root, on the Docker host:

```sh
sh scripts/restore.sh --yes backups/poresira-20261112T154040Z.dump
```

The script:

1. verifies the checksum file if present;
2. stops the application services;
3. drops and recreates the database, owned by the migration role;
4. runs `pg_restore`, then re-applies the default privileges (they are not stored in dumps);
5. restarts everything and runs `verify_audit_chain`.

### Rebuilding a lost server

1. Install Docker, clone the repository at the version you were running, and copy your `.env`
   (keep a copy of it with your backups; it holds the database passwords).
2. `docker compose up -d db`: a fresh volume creates the roles and an empty database.
3. `sh scripts/restore.sh --yes <backup file>`.
4. Compare the head hash printed at the end with the one you recorded.

The restored schema is whatever the backup held. If you restore an old backup under a newer
release, `docker compose up -d` applies the newer migrations automatically.

## Switching profiles

Profiles only decide which services run. The database is the same in all of them, so moving
from `poste` to `labo` is: `docker compose down`, change `COMPOSE_PROFILES`, `docker compose up -d`.
