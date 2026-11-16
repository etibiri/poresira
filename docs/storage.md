<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Storage

The `storage` app is the registry of the laboratory's data files: raw signal (POD5, FAST5), reads
(FASTQ, BAM), run reports and gel images. It records **where a file is, how big it is and its
checksums**, and can tell later whether the file is still the same. All tables are audited (see
[Architecture](architecture.md)).

PoreSira never stores the content of a file in the database, and it **never copies, moves,
renames or deletes** a file. It registers files where they are.

## Domain model

```text
StorageBackend ──< DataFile ──< FixityCheck
                      │
                      └── one of: SequencingRun · PcrRun · Sample
```

| Model | Identifier | Purpose |
|---|---|---|
| `StorageBackend` | none | A place where files live: a local disk, a mounted network share, or an offline medium |
| `DataFile` | `WBF-F26-00001-9` | A file: backend, relative path, kind, size, SHA-256, MD5, tier, integrity |
| `FixityCheck` | none | One check of a file against its recorded checksums |

A file belongs to **exactly one** record of its own project: a sequencing run (reads, signal,
report), a PCR run (a gel image) or a sample. A gel image used for several samples is attached to
its PCR run.

## Backends

| Kind | Root | Read by PoreSira |
|---|---|---|
| Local disk | absolute directory | yes |
| Network share (mounted) | absolute directory of the mount | yes |
| Offline medium | label of the medium, such as `Tape 3, shelf B` | no: files are catalogued |

An S3-compatible backend is planned: it will be one more kind behind the same `FileStore`
interface (`poresira.storage.backends`) and needs the `boto3` library, to be added when requested.

Declare a backend (platform managers and administrators):

```bash
python manage.py create_storage_backend "Lab NAS" --kind nas --root /mnt/nas/nanopore --user alice
```

The kind never changes. Moving the root (the disk was remounted elsewhere) needs a reason.

## Registering files

From the page of a sequencing run, a PCR run or a sample, **Register a file** takes a backend and a
path relative to its root. A disk or share file is read once: size, SHA-256 and MD5 are computed
together. For an offline medium you give the size and checksums yourself. The kind (`pod5`,
`fast5`, `fastq`, `bam`, `report`, `gel_image`, `other`) is guessed from the name and can be
overridden.

To register a whole run folder:

```bash
python manage.py register_files /mnt/nas/nanopore/20231109_run1 --backend "Lab NAS" \
    --user alice --sequencing-run WBF-Q26-00001-Y --dry-run
```

Use one of `--sequencing-run`, `--pcr-run` or `--sample` with the identifier of the record. The
project comes from that record. `--only pod5,fastq` keeps some kinds only. A dry run lists what
would be registered without reading any file.

- Hidden files are skipped; files already registered at the same place are skipped, so an
  interrupted run can be repeated.
- Each file is its own transaction: one refusal (a link leaving the backend, an unreadable file)
  does not stop the others. The failures are listed and the command ends with an error.
- Every file of the folder goes to the same record. Files are not tied to a single barcode.

## Integrity

A file is **intact** when it was hashed at registration and not found different since. A check
reads the file again and records one of:

| Outcome | Meaning |
|---|---|
| Intact | same size and SHA-256 as recorded |
| Changed | the file exists but differs; the check keeps the size and SHA-256 seen |
| Missing | nothing at that path any more |
| Unreadable | the file exists but could not be read |

The recorded values never change: a replaced file is registered as a new file. Each check is kept
in the file's history. **Check now** on the file page runs the check as a Celery task, because
hashing a large file takes minutes; reload the page afterwards. Files on an offline medium cannot
be checked.

!!! warning "The worker must see the files"
    The web server and the Celery worker must both reach the backend root **at the same path**. With
    Docker Compose, `PORESIRA_DATA_DIR` (default `/srv/poresira-data`) is mounted read-only in the
    `web` and `worker` services at the same path as on the host; use it, or a folder below it, as
    the root of the backend. For a NAS, mount the share on the host under that folder. A backend
    outside that folder is not visible to the containers. Without a worker (a development setup),
    set `CELERY_TASK_ALWAYS_EAGER=true` to run checks inside the request.

No periodic check is scheduled yet.

## Tiers

Hot, warm and cold are labels the laboratory keeps (for example cold for files copied to tape).
Changing a tier needs a reason. Nothing is moved automatically.

## Rules enforced by the database

- What identifies a file never changes: project, backend, path, kind, size, checksums and the
  record it belongs to.
- A path is relative and cannot leave the backend (`..` and a leading `/` are refused); the checksums
  are lowercase hexadecimal of the right length; a place holds one active file.
- A file belongs to one record, of its own project.
- Changing the tier or the root of a backend needs a reason.
- Fixity checks are never edited. Void, never delete: a backend that holds active files cannot be
  voided.

## Permissions

| Action | Who |
|---|---|
| See files and backends | members of the project (files), any signed-in user (backends) |
| Register, check, change tier, void a file | project lead or contributor, administrator |
| Declare or move a backend, void a backend | administrator, platform manager |

## Demonstration data

The demonstration project registers **no file**: the real data live outside the repository. Declare
a backend, then register a run folder with `register_files` to see the screens fill in.
