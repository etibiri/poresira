<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/) once it reaches 1.0.

## [Unreleased]

### Added

- Project foundation: Django 5.2 on PostgreSQL 16, uv, ruff, pre-commit, GitHub Actions.
- Custom user model with global roles; projects with per-project memberships; users only see
  the projects they belong to.
- UUIDv7 primary keys and human-readable identifiers (`WBF-S26-00123-K`) with a Damm check
  character, drawn from one PostgreSQL sequence per type and year.
- Audit trail with django-pghistory: request context (user, IP address, request ID, reason),
  a hash chain over audit entries, the `verify_audit_chain` command, and database privileges
  that keep the application role from altering audit tables.
- "Void instead of delete" mixin requiring a reason.
- Web interface (Bootstrap 5, HTMX, Alpine.js, all served locally): login, project list,
  project history, English and French, per-user language, AGPL source link and version
  in the footer, `/health/` endpoint.
- Docker Compose profiles `poste`, `labo` and `plateforme`; backup and restore scripts.
- `samples` app: collection events, samples with five kinds and lineage (field sample, living
  plant, DNA, RNA, amplification product), hierarchical storage locations with grid positions,
  immutable QC measurements. Critical changes (quantity, status, barcode, location, position)
  require a reason enforced by a database trigger; one active sample per position.
- Sample web interface: filtered list (HTMX), detail page with family tree, QC and audit history,
  forms to derive, move, change, measure and void; collection events; storage tree.
- `import_field_samples` (all-or-nothing CSV import) and `load_demo_data`, which builds a demo
  project from the 66 BioSamples of PRJNA1460897 and Table 3 of Tibiri et al. 2020.
- `SequenceRecord`: GenBank accessions linked to the sample that was sequenced, with BioSample and
  BioProject; the demo project links the 24 genomes PV947737-PV947760.
- `with_reason` and `acting_as` helpers for audited changes made outside a request.
- Complete French translation of the new screens.
- AGPL-3.0-or-later license, CLA, contribution guide, code of conduct, citation file.
