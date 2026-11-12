<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Contributing to PoreSira

Thank you for helping. Code quality, tests and documentation matter as much as features.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- Open an issue to discuss anything bigger than a small fix.
- **Sign the CLA.** Every external contribution needs it. The CLA Assistant bot comments on
  your first pull request with the instructions; the text is in [CLA.md](CLA.md). Pull
  requests cannot be merged before it is signed.
- Everything committed to the repository is in English: code, comments, commit messages,
  documentation, error and log messages. The interface has English source strings and a French
  translation.

## Set up

```sh
uv sync
cp .env.example .env              # replace the "change-me" values
docker compose --profile poste up -d db redis
set -a; . ./.env; set +a          # makes the database settings visible to pytest and manage.py
uv run python manage.py migrate
uv run pre-commit install
uv run pytest
```

PostgreSQL 16 is required for development too: auditing relies on PostgreSQL triggers, so
SQLite is not supported.

## Rules

- **Style:** `uv run ruff check .` and `uv run ruff format .`. Use explicit names (no `data`,
  `tmp`, `obj`, `result`); comment only to explain a non-obvious *why*; keep docstrings short.
- **License header:** every source file starts with `SPDX-License-Identifier: AGPL-3.0-or-later`
  in its comment syntax. A pre-commit hook checks this.
- **Dependencies:** only add dependencies with an AGPL-compatible license, and say why in the
  pull request. Report proprietary or incompatible ones instead of adding them.
- **Tests:** every model and every traceability rule has tests. Target at least 85% coverage on
  `core`, `samples`, `pcr` and `sequencing`.
- **Commits:** small and coherent, in [Conventional Commits](https://www.conventionalcommits.org/)
  format (`feat(core): ...`, `fix: ...`, `docs: ...`).
- **No secrets:** `.env.example` is versioned, `.env` is ignored.

## Traceability rules for new models

- Primary key: inherit `UUIDPrimaryKeyModel` (UUIDv7).
- Domain objects are never hard-deleted: inherit `VoidableModel`.
- Audited models: add `pghistory.track(...)`, then in a migration call `attach_audit_chain` and
  `restrict_audit_tables` from `poresira/core/audit_migrations.py` for the new event table.
- Editing a critical field requires a reason, recorded in the audit log.
- Timestamps are `timestamptz`; never store naive datetimes.

## Translations

Mark user-facing strings with `gettext` / `{% trans %}`, then:

```sh
uv run python manage.py makemessages -l fr --ignore ".venv/*" --ignore "staticfiles/*" --no-obsolete --add-location file
# translate the new entries in locale/fr/LC_MESSAGES/django.po
uv run python manage.py compilemessages
```

A test fails if the French catalogue has untranslated entries.

## Documentation

Docs live in `docs/` (MkDocs Material). Preview with `uv run --group docs mkdocs serve`.
