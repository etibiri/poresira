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
  BioProject; the demo project links 54 published sequences (viral genomes and satellites) of two studies.
- `pcr` app: primers, cycling programs, master mixes, assays, PCR runs in a 96-well plate or in
  1 to 96 tubes, reactions with controls, results, Ct and band size. Control-based validity of a
  run; results of a closed run change only with a reason; read-only identity enforced by triggers.
- PCR web interface: run list and page with a coloured plate or tube view, forms to plan, add
  reactions, enter results, close, reopen and void; lists of assays, primers and programs; PCR
  results on the sample page.
- `import_pcr_run`: all-or-nothing CSV import of a run.
- Demonstration data: 98 laboratory samples with NanoDrop readings, rolling circle amplification
  attempts and SPG1/SPG2 PCR results, and the PCR data of Tibiri et al. 2020 (primers, programs,
  assays, 150 reconstructed results).
- `sequencing` app (Oxford Nanopore): library kits, flow cell types, instruments, flow cells,
  libraries with samples under barcodes, runs and reads per barcode. A flow cell carries one run
  at a time and several in turn when reusable (single-use types such as a Flongle serve one);
  each run keeps its pores at start. A library is sealed before it is sequenced, and results of a
  completed run change only with a reason. One adapter per platform holds barcode and ID rules.
- Sequencing web interface: runs, libraries with their barcode table, flow cells, kits and
  instruments; forms to plan, start, enter reads, complete, abort, reopen and void; a Sequencing
  section on the sample page.
- `import_library`: all-or-nothing CSV import of a library.
- Demonstration data: the 53 sequenced RCA products on two MinION runs (November 2023, from the
  laboratory sheet) and one Flongle run (2022, from the MinKNOW files).
- `storage` app: a registry of data files (POD5, FAST5, FASTQ, BAM, reports, gel images) kept in
  place on a local disk, a mounted share or a catalogued offline medium. Each file has its
  size, SHA-256 and MD5, a tier (hot, warm, cold), and is attached to one sequencing run, PCR
  run or sample. Fixity checks compare the disk with the record and are kept in the file's
  history; PoreSira never copies, moves or deletes a file.
- Files interface: file list and page, forms to register, check (as a Celery task), change tier
  and void, a Files section on the sequencing run, PCR run and sample pages, French translation.
- `create_storage_backend` and `register_files`: declare a backend and register a whole run
  folder in place (resumable, with a dry run).
- `computing` app: compute jobs on a sequencing run (Dorado basecalling, Dorado demultiplexing,
  FAST5 to POD5 conversion) run through Slurm. Each job keeps its tool and version, validated
  parameters, the exact script submitted, its input files with the SHA-256 they had, and its
  results, registered in place. Parameters never reach a shell unquoted, a changed input stops the
  job before the tool starts, and a job's script and parameters are fixed once it leaves the draft.
- Slurm agent: submit, follow and cancel jobs with `sbatch`, `squeue`, `sacct` and `scancel`
  from the `slurm` Celery queue; the Dorado command may be a container prefix; `refresh_jobs`
  brings active jobs up to date.
- Compute interface: job list and page, Basecall, Demultiplex and Convert actions on the
  sequencing run page, script download, French translation.
- `create_demo_storage`: writes a fictitious, git-ignored data folder (79 placeholder files) for the
  demonstration runs and gel images and registers it as the backend "Demo disk".
- `with_reason` and `acting_as` helpers for audited changes made outside a request.
- Complete French translation of the new screens.
- AGPL-3.0-or-later license, CLA, contribution guide, code of conduct, citation file.
