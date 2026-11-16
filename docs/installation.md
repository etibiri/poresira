<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Installation

## Requirements

- Linux, macOS or Windows with WSL2.
- Docker Engine with the Compose plugin, installed from docker.com. Avoid the snap package: its
  confinement can block Compose from stopping containers.
- About 2 GB of free disk for images and the database (sequencing files are stored separately).

No internet access is needed at run time. All front-end libraries ship with the application.

## Install

```sh
git clone https://github.com/etibiri/poresira.git
cd poresira
cp .env.example .env
mkdir backups        # create it yourself: Docker would create it owned by root
```

Edit `.env` and replace every `change-me`:

- `DJANGO_SECRET_KEY`: a long random string, for example `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`.
- `POSTGRES_SUPERUSER_PASSWORD`, `POSTGRES_OWNER_PASSWORD`, `POSTGRES_APP_PASSWORD`: three
  different passwords. Use letters and digits only: they are inserted into SQL at first start.
- `PORESIRA_SITE_PREFIX`: your site prefix in identifiers (`WBF` gives `WBF-S26-00123-K`).

Then start the profile that fits your setup.

## Profiles

| Profile | Use it for | Services |
|---|---|---|
| `poste` | one computer, local disk | database, Redis, web, Celery worker |
| `labo` | a lab server with a NAS | `poste` plus scheduled database backups |
| `plateforme` | a lab connected to an HPC cluster | `labo` plus the Slurm agent queue worker |

```sh
docker compose --profile poste up -d
docker compose --profile labo up -d
docker compose --profile plateforme up -d
```

Or set `COMPOSE_PROFILES=poste` in `.env` and run `docker compose up -d`. Changing profile is
a configuration change: stop the stack, change the profile, start it again. The database is
the same in every profile.

The web interface listens on `http://localhost:8000` only. To serve the lab network set
`PORESIRA_BIND_ADDRESS=0.0.0.0` and, if you do not terminate HTTPS in front of it,
`PORESIRA_HTTPS=false` (otherwise browsers will not keep the secure-only session cookie).
Put a reverse proxy with TLS in front of it for anything beyond a trusted local network.

## First user

```sh
docker compose exec web python manage.py createsuperuser
```

Then sign in at `/admin/` to create users and projects, or at `/` for the interface.

## Check it works

```sh
curl http://localhost:8000/health/          # {"database": "ok", "redis": "ok", "status": "ok"}
docker compose exec web python manage.py verify_audit_chain
```

## Upgrade

```sh
git pull
docker compose up -d --build
```

The `migrate` service applies database migrations before the web service starts.

## Development setup

See [CONTRIBUTING.md](https://github.com/etibiri/poresira/blob/main/CONTRIBUTING.md).
