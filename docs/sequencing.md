<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Sequencing

The `sequencing` app records which samples were put in a library, under which barcode, and which
flow cell and run read that library. It covers Oxford Nanopore; other platforms will plug in
through an adapter (see below). All tables are audited (see [Architecture](architecture.md)).

## Domain model

```text
LibraryKit ──< Library ──< LibraryMember >── Sample
                  │              │
                  └──< SequencingRun ──< BarcodeResult
FlowCellType ──< FlowCell ──┘      Instrument ┘
```

| Model | Identifier | Purpose |
|---|---|---|
| `LibraryKit` | none | Kit code (`SQK-NBD114.96`), chemistry, number of barcodes |
| `FlowCellType` | none | Product code (`FLO-MIN114`), pore, and whether the flow cell is reusable |
| `Instrument` | none | A sequencer: name, model, serial number |
| `FlowCell` | none | One physical flow cell, by its serial number (`FAT93267`) |
| `Library` | `WBF-B26-00003-2` | Samples prepared together with one kit |
| `LibraryMember` | none | A DNA extract, RNA extract or amplification product, and its barcode |
| `SequencingRun` | `WBF-Q26-00001-Y` | One library on one flow cell, with the basecalling |
| `BarcodeResult` | none | Reads (and optionally bases, N50, mean quality) of one member in one run |

Kits, flow cell types, instruments and flow cells are shared by all projects. Libraries, runs and
results belong to a project.

## A library and a run, step by step

1. **Draft library.** Choose the kit and add samples with their barcodes (`7`, `07`, `NB07`,
   `BC07` and `barcode07` all mean barcode 7). A barcode is used once per library and must exist
   in the kit.
2. **Sealed library.** The content is fixed. A sample in a sealed library cannot be voided.
3. **Planned run.** Choose a flow cell and, if known, the instrument and the active pores counted
   at the check.
4. **Running.** Give the start date and, if known, the run ID and the acquisition software.
5. **Reads.** Enter the reads of each member (bases, N50 and mean quality are optional).
6. **Completed.** Give the basecaller and its version (and the model, the reads and bases of the
   run). After that, a result changes only with a reason, and the run reopens only with a reason.

A run can be **aborted** with a reason. Reads are counted per member and cannot exceed the reads
of the run.

## Flow cells are reused

A flow cell carries **one run at a time**, and several in turn when its type is reusable (MinION
and PromethION cells are washed and reloaded). A single-use type (a Flongle) serves one run,
unless that run was aborted. Each run keeps its own `pores_at_start`, so the decline of a reused
flow cell can be followed run after run. A completed run cannot be reopened while another run is
in progress on the same flow cell.

## Rules enforced by the database

- Identity is read-only: the flow cell ID and type, the project, library and flow cell of a run,
  the library and sample of a member, the run and member of a result.
- A member belongs to the project of its library, is a DNA extract, RNA extract or amplification
  product, has a barcode inside the kit (except in a reconstructed library) and joins a draft
  library only.
- A run needs a sealed library of its own project; one run in progress per flow cell; a
  single-use flow cell serves one run.
- A result belongs to a member of the library of its run; one result per member and run.
- Changing a result of a completed run needs a reason.
- Void, never delete: a library with members or runs, a member with results, a run with results,
  a flow cell, kit, type or instrument used by an active record cannot be voided.

## Permissions

| Action | Who |
|---|---|
| See libraries, runs and results | members of the project, administrators |
| Create libraries, add samples, seal, plan, start, enter reads, complete, abort, reopen, void | project lead or contributor, administrator |
| Register a flow cell | anyone who can edit some project, platform managers |
| Create kits, flow cell types, instruments | administrator, platform manager (through the services) |

Kits, flow cell types and instruments are listed in the web interface but created through
`poresira.sequencing.services` (or the demonstration loader); there is no creation form yet.

## Adapters

`poresira.sequencing.adapters` holds what depends on the platform: how a barcode is written, how
a flow cell ID and a run ID are normalised, which kits make sense. Only
`NanoporeAdapter` exists. Adding a platform means adding an adapter and its kits, not changing
the models.

## Importing a library

```bash
python manage.py import_library library.csv --project DEMO-SPBF --kit SQK-NBD114.96 \
    --user alice --name "RCA products, run 1" --prepared-on 2023-11-05 --seal --dry-run
```

Columns: `sample` (identifier, or an unambiguous label in the project), `barcode`, `input_ng`,
`notes`. `--reconstructed` marks a library taken from a record. The import is all-or-nothing and
lists every problem with its line number. Runs are planned in the web interface or through the
services.

## Demonstration data

`load_demo_data` adds three kits for the Front Microbiol 2026 chemistries, the Flongle kit named
in the MinKNOW files, three flow cell types (MinION, PromethION, Flongle), two instruments and
three runs of the sweetpotato RCA products, **53 samples in all**:

| Flow cell | Date | Samples | Source |
|---|---|---|---|
| FAT93267 (MinION) | 2023-11-09 | 24, `BC01`-`BC24` | laboratory e-mail and barcode sheet |
| FAV02927 (MinION) | 2023-11-11 | 28, `BC25`-`BC52` | laboratory e-mail and barcode sheet |
| AMF735 (Flongle) | 2022-11-04 | BFA316, not barcoded | MinKNOW files |

The two MinION runs are marked reconstructed: they come from an e-mail and a sheet, not from the
sequencer, so their reads per barcode are unknown. The Flongle run comes from the MinKNOW output
(530 760 reads, 462.7 Mb basecalled, Guppy 5.1.13). The thesis document calls BFA316 `BC53`, but
that run was not barcoded and the demo keeps it so; the run notes say so. Its MinKNOW protocol
group, `Equiphi29`, is kept as recorded. Reads files (POD5, FASTQ) are not stored yet: they
wait for the `storage` app.
