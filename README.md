<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# PoreSira

Open-source laboratory information management system (LIMS) that traces every biological
sample from field collection to public data deposition: collection, samples and derivatives,
PCR, sequencing (Oxford Nanopore first), data files, compute jobs, and ENA/SRA submission.

PoreSira is built for field-to-sequence laboratories that work with limited connectivity:
everything runs from local Docker containers and no asset is loaded from the internet.

> **Status: phase 5 (storage).** Foundation (users, projects, access control, identifiers, audit
> trail, deployment profiles), samples (collection events, derivatives and lineage, storage
> locations, QC, CSV import, demonstration data), PCR (primers, assays, runs in plates or
> tubes, results, control validity) and sequencing (libraries and barcodes, reusable flow cells,
> runs, reads per barcode; Oxford Nanopore) and storage (data files registered in place, with
> checksums and integrity checks) are in place. Compute jobs and deposits are not implemented yet.

## What the foundation gives you

- **Traceability by design.** Every change to a tracked record is written to an append-only
  audit log by PostgreSQL triggers, with who, from where, when and why. Audit entries are
  hash-chained and `manage.py verify_audit_chain` reports any break.
- **No hard deletes.** Records are voided with a mandatory reason.
- **Readable identifiers** such as `WBF-S26-00123-K`, with a check character that catches
  typos and swapped digits.
- **Per-project access.** Users only see the projects they belong to.
- **English and French** interface, with a per-user language.
- **Three deployment profiles** (`poste`, `labo`, `plateforme`) from one Compose file.

## Quick start

Requires Docker with the Compose plugin (install it from docker.com; the snap package is
known to cause permission problems).

```sh
git clone https://github.com/etibiri/poresira.git && cd poresira
cp .env.example .env        # then replace every "change-me" value
docker compose --profile poste up -d
docker compose exec web python manage.py createsuperuser
```

Open <http://localhost:8000>. Other profiles:

| Profile | Command | Adds |
|---|---|---|
| `poste` | `docker compose --profile poste up -d` | single computer, local disk |
| `labo` | `docker compose --profile labo up -d` | unattended database backups |
| `plateforme` | `docker compose --profile plateforme up -d` | Slurm agent queue worker |

See the [documentation](docs/index.md) for installation, configuration, architecture and
the backup and restore procedure.

## Development

```sh
uv sync
docker compose --profile poste up -d db redis
set -a; . ./.env; set +a
uv run pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

PoreSira is free software under the
[GNU Affero General Public License v3.0 or later](LICENSE). If you run a modified version as a
network service, the AGPL requires you to offer its source to your users; the interface footer
links to the source and shows the deployed version for that purpose.

Contributions require signing the [CLA](CLA.md).

## Citing

See [CITATION.cff](CITATION.cff).

## Maintainer

Ezechiel B. Tibiri, INERA/CNRST, WAVE Research Burkina Faso.
