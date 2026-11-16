<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Installation

## Requirements

- Linux, macOS or Windows with WSL2.
- Docker Engine with the Compose plugin, installed from docker.com. Avoid the snap package: its
  confinement can block Compose from stopping containers.
- About 2 GB of free disk for images and the database (sequencing files are stored separately).

No internet access is needed at run time. All front-end libraries ship with the application.

## Install on macOS (Apple silicon, M1 and later)

PoreSira runs on a Mac with an Apple chip through Docker: every image it uses (Python, PostgreSQL,
Redis) has an `arm64` build, and every compiled Python library in `uv.lock` has an `aarch64`
wheel, so nothing is emulated.

1. Install [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) (Apple
   silicon version), or [OrbStack](https://orbstack.dev/) or Colima, then start it. Compose is
   included. In Docker Desktop, give it at least 4 GB of memory.
2. Install the tools the commands below use: `git` (with the Xcode command line tools,
   `xcode-select --install`) and `python3`, which the same tools provide.
3. Follow [Install](#install) below. On macOS:
   - run the commands in Terminal, from the cloned folder;
   - **put the data folder under `/Users`**, for example `PORESIRA_DATA_DIR=/Users/you/poresira-data`
     (create it first). Docker Desktop shares `/Users`, `/Volumes`, `/private` and `/tmp` by
     default; any other folder must be added in *Settings → Resources → File sharing*;
   - leave `BACKUP_OWNER` empty, or set it to your own ids (`id -u`:`id -g`, usually `501:20`):
     the default `1000:1000` does not exist on a Mac;
   - port 5000 is used by AirPlay on recent macOS, but PoreSira uses 8000, so no change is needed;
   - `scripts/restore.sh` uses `shasum` when `sha256sum` is missing.
4. Open `http://localhost:8000`.

What does **not** run on a Mac: Dorado on a GPU (basecalling goes through the Slurm agent on a
cluster) and the Slurm agent itself. The Docker `poste` and `labo` profiles are fine.

To work on the code, follow
[CONTRIBUTING.md](https://github.com/etibiri/poresira/blob/main/CONTRIBUTING.md) after
`brew install uv gettext`: the database and Redis still run in Docker.

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
