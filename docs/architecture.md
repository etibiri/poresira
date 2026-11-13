<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Architecture

PoreSira is a Django 5.2 application on PostgreSQL 16, with Celery and Redis for background
work. Server-rendered templates use Bootstrap 5, HTMX and Alpine.js, all served from the
application itself.

## Apps

| App | Role | Status |
|---|---|---|
| `core` | users, projects and memberships, roles, identifiers, audit | implemented |
| `samples` | collection events, samples, derivatives, lineage, storage locations, QC (see [Samples](samples.md)) | implemented |
| `pcr` | primers, cycling programs, master mixes, assays, runs in plates or tubes, results (see [PCR](pcr.md)) | implemented |
| `sequencing` | libraries, pools, flow cells, runs; one adapter per platform | planned |
| `storage` | file registry and storage backends | planned |
| `computing` | compute jobs, Slurm agent | planned |
| `deposits` | ENA/SRA submissions, RO-Crate export | planned |

## Access control

Each user has a global role (administrator, platform manager, technician, bioinformatician,
project lead, guest). Access to data is per project through `ProjectMembership`, which carries
a project role (lead, contributor, viewer). `Project.objects.visible_to(user)` is the only
way views should obtain projects: members see their projects, administrators and superusers
see all, anonymous users see none. Voided memberships grant nothing.

## Identifiers

- **Primary keys** are UUIDv7 (`poresira.core.uuid7`), time-ordered and generated in Python.
- **Human-readable identifiers** look like `WBF-S26-00123-K`: site prefix, object type
  letters, two-digit year, counter, check character.
- The counter comes from a PostgreSQL sequence per type and year, created on first use by the
  `poresira_next_identifier_counter` function. Sequences never go back, so an identifier is
  never reused. A rolled-back transaction leaves a gap in the numbering, never a duplicate.
- The check character is a Damm check over a 32-symbol alphabet (digits and letters without
  I, L, O, U). It detects any single-character error and any swap of two adjacent characters.
  It covers the type, year and counter, not the site prefix.

## Audit trail

```text
HTTP request
  -> AuditContextMiddleware: user, IP address, request ID, reason (set on the session)
ORM write on a tracked model
  -> pghistory trigger        -> event table
  -> hash-chain trigger       -> core_auditchainlink
```

1. **Triggers, not application code.** django-pghistory records every insert and real update
   of `Project` and `ProjectMembership` in event tables, whatever code or SQL made the change.
2. **Request context.** `AuditContextMiddleware` attaches the user, URL, IP address, request ID
   and, when provided (`reason` form field or `X-Audit-Reason` header), the reason.
3. **Hash chain.** A trigger appends one link per event row:
   `sha256(previous hash | event table | event id | row as JSON)`. Writers are serialised by an
   advisory lock. `manage.py verify_audit_chain` recomputes the whole chain inside the
   database and reports altered rows, missing rows, removed links and unlinked rows. It also
   prints the head hash; record it outside the server to detect truncation of the newest entries.
4. **Privileges.** The application role has no UPDATE, DELETE or TRUNCATE on audit tables, and
   no INSERT on the context and chain tables, which only database functions write. Two roles
   are therefore required: the owner runs migrations, the application role runs the site.

!!! note "What the chain does and does not prove"
    The chain makes tampering detectable by anyone who can read the database; it does not prevent
    a database administrator from rewriting both the rows and the hashes. Keep the head hash and
    your backups somewhere the administrator cannot rewrite.

### Tracking a new model

Add `pghistory.track(...)` to the model, then use `attach_audit_chain` and
`restrict_audit_tables` from `poresira/core/audit_migrations.py` in a migration for its event
table.

## Business rules in the database

Rules that protect traceability live in PostgreSQL, not only in Python: `RequireReason`
triggers (`poresira.core.pgtriggers`) reject a change to a critical field unless the audit
context carries a reason, `ReadOnly` triggers freeze identity fields, and partial unique
constraints keep labels, barcodes and storage positions unique. Code outside a request sets the
reason and the actor with `with_reason` and `acting_as` (`poresira.core.reason`); web forms
supply the reason through the audit middleware. Views never write to models directly: they call
the functions in each app's `services.py`, which check permissions and rules.

## Void instead of delete

`VoidableModel` gives a record `voided_at`, `voided_by` and `void_reason`. `void(user=..., reason=...)`
refuses an empty reason. Deleting is blocked three ways: `delete()` raises, queryset `delete()`
raises, and a PostgreSQL trigger rejects `DELETE`. A check constraint rejects a voided record
without a reason.

## Internationalisation and time

Source strings are English; `locale/fr` holds the complete French translation. Language is the
user's profile choice, then a cookie, then `PORESIRA_DEFAULT_LANGUAGE`. Timestamps are
`timestamptz`, stored in UTC and displayed in `PORESIRA_TIME_ZONE`.

## Licensing and source link

PoreSira is AGPL-3.0-or-later. Every page footer links to the source and shows the deployed
version, which satisfies AGPL section 13 for network use.

## Deployment

One `docker-compose.yml` with three profiles. All run the same images and the same database.

| Service | Profiles | Purpose |
|---|---|---|
| `db` | all | PostgreSQL 16, creates the owner and application roles on first start |
| `redis` | all | Celery broker (Redis 7.2, the last BSD-licensed release) |
| `migrate` | all | applies migrations as the owner role, then exits |
| `web` | all | gunicorn, as the application role |
| `worker` | all | Celery worker on the default queue |
| `backup` | `labo`, `plateforme` | scheduled `pg_dump` |
| `slurm-agent` | `plateforme` | Celery worker on the `slurm` queue (placeholder until the `computing` app) |

### Storage rules (for upcoming phases)

The database never stores sequencing file contents, only path, backend, size, SHA-256 and MD5.
The `storage` app will reach files through a `StorageBackend` interface; open backends (local
disk, NAS mount, S3-compatible, catalogued offline media) are enough to run the application.
