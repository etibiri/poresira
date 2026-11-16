<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Deposits

The `deposits` app sends the data of a sequencing run to the European Nucleotide Archive (ENA),
or prepares the files to submit them to NCBI, records the accessions the archive gives back, takes
into account what the public archives already hold, and exports a Research Object Crate (RO-Crate)
that describes the data and how they were made. All tables are audited (see [Architecture](architecture.md)).

!!! danger "A real deposit is final"
    Once the archive accepts a real deposit, its accessions are permanent and the data become
    public on the release date. PoreSira therefore makes every deposit a **test** first, and the
    real deposit a separate step with its own confirmations.

## Domain model

```text
Deposit ──< DepositItem (study · sample · reads) ──< DepositFile >── DataFile
PublicAccession  (BioProject · BioSample · SRA experiment · SRA run)
```

| Model | Identifier | Purpose |
|---|---|---|
| `Deposit` | `WBF-X26-00001-1` | What a project sends to the archive: environment, status, title, release date, library settings, receipt |
| `DepositItem` | none | One object sent: the study, a sample (BioSample), or the reads of a sample (experiment and run) |
| `DepositFile` | none | A FASTQ or BAM file of a reads item, with its SHA-256 when the deposit was prepared |
| `PublicAccession` | none | An accession that already exists in a public archive for a project or a sample |

A deposit belongs to a project and is built from **one sequencing run**.

## What is already public

The archives exchange their records: an NCBI BioSample can be cited in an ENA submission, and the
reads of an SRA run are in ENA too. A deposit must not register twice what already exists, so
PoreSira records it and checks it.

- **`PublicAccession`** holds a BioProject, the BioSample of a field sample or living plant, and
  its SRA experiments and runs, with the date they were released. Record them from a CSV:

  ```bash
  python manage.py import_public_accessions accessions.csv --project DEMO-SPBF --user alice --dry-run
  ```

  Columns: `sample` (identifier, or label; empty for a BioProject), `kind` (`bioproject`,
  `biosample`, `sra_experiment`, `sra_run`), `accession`, `released_on`, `notes`. All or nothing,
  with the line number of every problem. A label that names a field sample and the plant grafted
  from it means the field sample.
- **GenBank sequences** are the `SequenceRecord`s already in PoreSira; the BioSample written on
  them also counts.
- **In a real deposit**, a sample that has a BioSample is **cited, not registered again**: its
  item starts as accepted with that accession and the reads use it. Reads of a sample that is
  already in SRA are an **error**: do not send them twice. Leave them out of the deposit with
  **Leave out**, or tell PoreSira what is new.
- **In a test deposit** the same findings are warnings, and temporary samples are registered:
  the test service is discarded, and it does not know the real BioSamples.
- GenBank sequences and an existing BioProject of the project are mentioned as warnings. The
  RO-Crate links them, the BioSample and the SRA runs to the public archives.

## Linking PoreSira to the public archives

Once data are deposited, PoreSira can read the archives and keep its records in step:

```bash
python manage.py sync_public_archives DEMO-SPBF --bioproject PRJNA1460897 --user alice          # dry run
python manage.py sync_public_archives DEMO-SPBF --bioproject PRJNA1460897 --user alice --apply
```

- **Reading only.** PoreSira never writes to the archives and never changes a sample, a sequence or
  a BioSample. Only public accession numbers leave the computer (and the tool name `poresira`; no
  e-mail address is sent). Run it where the Internet is reachable, for example from cron.
- **SRA**: NCBI (E-utilities) or ENA (Portal API, where SRA runs are mirrored) give the runs of the
  BioProject. `--source auto` (the default) tries NCBI then ENA. A run is matched to its sample by a
  BioSample the project already knows, otherwise by the label of the isolate (a field sample wins
  over a plant with the same label). The BioProject, BioSamples, experiments and runs that are new
  are recorded as `PublicAccession`s with `--apply`; a run with no sample is reported, not invented.
- **GenBank** records have no link to the BioProject or to the BioSample at NCBI. PoreSira checks
  each of its sequences by accession: not found, different length, different isolate.
- **BioSamples** are compared with the project: collection date, host and coordinates.
- **Gaps** are named: sequenced samples with no SRA run.
- **Limits**: NCBI allows about three requests a second without a key (the reader waits and
  retries); set `PORESIRA_NCBI_API_KEY` for more. `PORESIRA_NCBI_EUTILS_URL`,
  `PORESIRA_ENA_PORTAL_URL`, `PORESIRA_PUBLIC_MIN_INTERVAL` and `PORESIRA_PUBLIC_TIMEOUT` tune it.

The sample page links each BioSample, SRA run and GenBank record to NCBI and, for BioSamples and runs,
to ENA, which mirrors them.

## A deposit, step by step

1. **Prepare.** On the page of a sequencing run, **Prepare a deposit**: title, description,
   sample checklist, library source, selection and strategy. PoreSira creates the study, one
   sample per field sample (a greenhouse plant has its own) and the reads of each library member,
   with the FASTQ files (or BAM files if there is no FASTQ) whose path names the barcode
   (`barcode07`, `BC07`, `NB07`). A run that was not barcoded gives all its files to its only
   sample. A member without a file is kept so that the report names it.
2. **Check.** The report lists **errors** that stop the submission (collection date, country,
   host species and taxon ID missing; reads file absent, changed, voided or on an offline medium;
   FASTQ and BAM mixed; run not completed; instrument unknown to the archive; reads already in
   SRA in production) and **warnings** (run reconstructed from a record, coordinates finer than
   0.01° that would publish farm locations, GenBank sequences, an existing BioProject). The
   collector's name is never sent.
3. **Read what will be sent.** **Files to be sent** downloads the XML and the manifests; the
   accessions the archive has not given yet are placeholders.
4. **Test.** **Submit to the test service**: the deposit agent registers the study and the samples,
   then submits the reads of each sample. Each accession is recorded as the archive answers.
5. **Real.** On an accepted test deposit, a project lead or administrator makes **Prepare the real
   deposit**: a release date in the future and the confirmation that sampling and benefit-sharing
   permits were checked (Nagoya Protocol). This is a new deposit; studies and samples already
   accepted in production keep their accessions, test accessions are never reused. **Submit for
   real** then asks for the project code to be typed.

## NCBI

NCBI has no open service that PoreSira can send to. Its programmatic route is a file-transfer
account that NCBI creates for a group (with a production and a test folder), which PoreSira does not
use yet. So an **NCBI deposit is prepared by PoreSira and submitted by a person** in the
[Submission Portal](https://submit.ncbi.nlm.nih.gov/about/sra/):

1. Choose **NCBI (Submission Portal)** as the archive when preparing the deposit. The same checks
   run, and reads already in SRA are an error at once (there is no test step).
2. **Files for the Submission Portal** downloads:
   - `biosample_attributes.tsv`: one row per BioSample to create, with the columns of the package
     `MIMS.me.plant-associated.6.0` (the package of the laboratory's existing BioSamples):
     `sample_name`, `sample_title`, `organism` (a metagenome: `plant metagenome` by default, set
     `PORESIRA_NCBI_ORGANISM` to change it), `isolate`, `host`, `collection_date`, the three
     `env_*` contexts (`not applicable`, as in the accepted records), `geo_loc_name`
     (`Burkina Faso: Nahouri, Tiebele`) and `lat_lon` (`11.08 N 0.90 W`). A BioSample that
     already exists is not listed.
   - `sra_metadata.tsv`: one row per experiment, with the library values, `OXFORD_NANOPORE`, the
     instrument (`MinION`), the file type and `filename`, `filename2`… columns; an existing
     BioSample is cited by its accession.
   - `files_to_upload.txt` and `README.txt` with the steps.
3. Submit them in the portal, then **record the accessions** NCBI gives with
   `import_public_accessions`, so that no later deposit sends the same data again.

NCBI requires coordinates and file names that are unique in a submission; both are errors in the
report. The collector's name is not in any file.

!!! warning "To check against NCBI's template"
    The BioSample mandatory attributes come from NCBI's page for the package. The **SRA column
    names** were written from NCBI's documentation but not confirmed (the exact list is in the
    Excel template NCBI gives you), and the portal documents Excel templates, not `.tsv` upload:
    copy the columns into the template or paste them in the portal's sheet. The columns are one
    constant, `SRA_COLUMNS` in `poresira/deposits/ncbi.py`; edit it if a name differs.

An archive is chosen once: the archives exchange their records, so **a deposit goes to one archive
only**. A trial on NCBI validates the files and the portal steps, it is not a second deposit.

## Statuses and rules

`Draft` → `Being submitted` → `Accepted` or `Rejected`. A deposit is accepted only if every object
is. A failure for one reads item does not stop the others, and what the archive accepted keeps its
accession. A rejected real deposit that was partly accepted is **not retried**: prepare a new
deposit, which reuses the accessions already given.

The database enforces:

- a **production deposit has a release date and the permits confirmed**;
- once the draft is left, what was sent (environment, title, release date, checklist, library
  values, permits) never changes, and a finished deposit stays finished;
- items are added while the deposit is a draft, and belong to its project; reads depend on a
  sample of the same deposit; files are FASTQ or BAM files of the project;
- an accession has the archive's form (`PRJ…`, `SAM…`, `ERR…`, `ERX…`), is recorded only while the
  deposit is being submitted, and never changes; an alias is unique in the deposit;
- a public accession has its archive's form, a sample of the same project (none for a BioProject)
  and is recorded once.

## Sending: the deposit agent

Only the **deposit agent**, a Celery worker on the `deposits` queue, reaches the archive. Give it
the Webin account in **its** environment and nowhere else:

```bash
ENA_WEBIN_USER=Webin-12345 ENA_WEBIN_PASSWORD=… \
    celery -A config worker --queues deposits --hostname deposits@%h
```

| Variable | Default | Purpose |
|---|---|---|
| `ENA_WEBIN_USER`, `ENA_WEBIN_PASSWORD` | empty | The Webin account (never stored, never logged) |
| `ENA_WEBIN_CLI` | `webin-cli` | The Webin-CLI command, which may be several words (`java -jar …`) |
| `ENA_WORK_DIR` | `<PORESIRA_DATA_DIR>/deposits` | Manifests and Webin-CLI reports |
| `ENA_TEST_URL`, `ENA_PRODUCTION_URL` | the archive's drop-box addresses | The submission service of each environment |
| `ENA_TIMEOUT_SECONDS`, `ENA_CLI_TIMEOUT_SECONDS` | `120`, `21600` | Time limits |

- The study and the samples go as XML to the Webin submission service, with the release date; the
  reads go through [Webin-CLI](https://ena-docs.readthedocs.io/en/latest/submit/general-guide/webin-cli.html),
  which validates them and uploads the files. Webin-CLI needs Java; the agent must see the data
  folder at the same path as the web server.
- Credentials travel over HTTPS only (HTTP is accepted to this computer, for tests).
- **The password is visible to other users of the agent's computer.** Webin-CLI documents no other
  way than `-password` to give it, so it is in the process list while a submission runs. Use an
  account made for PoreSira and a computer you control.
- The archive's answers are read defensively (no `DOCTYPE` or entity, size limit) and every text
  stored or shown has the password removed.

!!! warning "Not run against the real archive"
    The code was tested against a local stand-in for the submission service and for Webin-CLI.
    The Webin-CLI pages do not document its exit codes, the name of its receipt or its console
    lines, so success is read from a receipt XML in `<output>/reads/<name>/submit/` marked
    `success="true"`, and the first `RUN` and `EXPERIMENT` accessions are taken from it. Confirm
    this on the **test** service before any real deposit, together with the sample attribute names
    of the default checklist.

## RO-Crate

**RO-Crate** on a deposit downloads a zip with `ro-crate-metadata.json` describing the project's
samples and their attributes, the files (name, size, SHA-256), each sequencing (instrument, kit,
basecaller) and the successful compute jobs (tool, version, parameters, inputs, outputs), with links
to the archives. The files are described, not included, and no person is named. The licence is left
empty unless you set `PORESIRA_CRATE_LICENSE`.

## Permissions

| Action | Who |
|---|---|
| See deposits and download their files | members of the project, administrators |
| Prepare, submit to the test service, leave out, void | project lead or contributor, administrator |
| Prepare the real deposit and submit it | project lead, administrator |
| Record public accessions | project lead or contributor, administrator |

## Demonstration data

`load_demo_data` records the accessions NCBI holds for the demonstration project: the BioProject
`PRJNA1460897`, its 66 BioSamples and its 66 SRA runs and experiments (released on 2026-05-01).
A deposit prepared for a demonstration run therefore shows its reads as already in SRA.
