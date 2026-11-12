<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Configuration

Everything is configured through environment variables, normally in `.env` (copy it from
`.env.example`; never commit it).

## Application

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.dev` | `config.settings.prod` in containers. |
| `DJANGO_SECRET_KEY` | none | Required in production. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated host names the site answers to. |
| `DJANGO_DEBUG` | `true` in dev | Never enable in production. |
| `PORESIRA_HTTPS` | `true` | Secure-only cookies. Set `false` when serving plain HTTP on a local network. |
| `PORESIRA_TRUST_X_FORWARDED_FOR` | `false` | Take the audit IP address from `X-Forwarded-For`. Enable only behind a proxy that overwrites it. |
| `PORESIRA_DEFAULT_LANGUAGE` | `en` | Interface language for users who have not chosen one (`en` or `fr`). |
| `PORESIRA_TIME_ZONE` | `Africa/Ouagadougou` | Display time zone. Timestamps are stored in UTC. |
| `PORESIRA_SITE_PREFIX` | `WBF` | Prefix of human-readable identifiers (1-8 capital letters or digits). |
| `PORESIRA_SOURCE_URL` | the GitHub repository | Target of the footer "Source" link (AGPL section 13). Change it if you run a modified fork. |
| `PORESIRA_VERSION` | `dev` | Version shown in the footer. Set to the tag or commit hash you deploy. |

## Database and queue

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_DB` | `poresira` | Database name. |
| `POSTGRES_HOST`, `POSTGRES_PORT` | `localhost`, `5432` | Set to `db` and `5432` by Compose. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD` | app role | Role the process connects as. |
| `POSTGRES_SUPERUSER_PASSWORD` | none | Container superuser, used only at first start. |
| `POSTGRES_OWNER_USER`, `POSTGRES_OWNER_PASSWORD` | `poresira_owner` | Role that runs migrations and owns the tables. |
| `POSTGRES_APP_USER`, `POSTGRES_APP_PASSWORD` | `poresira_app` | Role the web and worker use. It cannot modify audit tables. |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker and health check. |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Run tasks in-process (tests). |

!!! warning
    Migrations strip write access to audit tables from `POSTGRES_APP_USER`. If that role does
    not exist, or is the role running the migrations, the migration prints a warning and the
    protection is **not** applied. Keep two separate roles, as Compose does.

## Compose

| Variable | Default | Meaning |
|---|---|---|
| `COMPOSE_PROFILES` | none | `poste`, `labo` or `plateforme`. |
| `PORESIRA_PORT` | `8000` | Published web port. |
| `PORESIRA_BIND_ADDRESS` | `127.0.0.1` | `0.0.0.0` to serve the lab network. |
| `BACKUP_DIR` | `./backups` | Host folder for backups. Prefer another disk or the NAS. |
| `BACKUP_KEEP_DAYS` | `30` | Older backups are deleted. |
| `BACKUP_INTERVAL_HOURS` | `24` | Delay between scheduled backups (`labo`, `plateforme`). |
| `BACKUP_OWNER` | none | `uid:gid` that should own backup files on the host. |

## Language and time zone

Each user can switch language from the navigation bar; the choice is stored on their profile.
Anonymous visitors get a cookie. The browser's `Accept-Language` is ignored on purpose, so
`PORESIRA_DEFAULT_LANGUAGE` is what a new user sees.
