<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Samples

The `samples` app records where a sample came from, what was made from it, where it is stored
and how good it is. Every table below is audited like the rest of PoreSira (see
[Architecture](architecture.md)).

## Domain model

```text
Project ──< CollectionEvent ──< Sample (field sample)
                                  └─< Sample (derivative, via parent) ──< ...
StorageLocation (tree) ──< Sample      Sample ──< QcMeasurement
```

| Model | Identifier | Purpose |
|---|---|---|
| `CollectionEvent` | `WBF-C26-00012-K` | A field visit: date (optional), place, coordinates, collector, notes |
| `Sample` | `WBF-S26-...`, `P`, `D`, `R`, `A` | One physical item; `kind` decides the type letter |
| `QcMeasurement` | none | One reading (NanoDrop, Qubit, Qubit RNA IQ, gel, other) with an outcome |
| `StorageLocation` | none | A node of the storage tree: room, freezer, fridge, cabinet, shelf, rack, box |

Sample kinds: field sample (`S`), living plant (`P`), DNA extract (`D`), RNA extract (`R`),
amplification product (`A`). Statuses: registered, stored, in process, consumed, discarded.

## Lineage

A derivative points to its parent. Allowed derivations:

| Parent | May give |
|---|---|
| field sample | living plant, DNA extract, RNA extract |
| living plant | living plant (grafting, propagation), DNA extract, RNA extract |
| DNA extract | DNA extract (dilution, aliquot), amplification product |
| RNA extract | RNA extract |
| amplification product | amplification product |

A derivative inherits project, collection event and host information from its parent. The
ancestors and descendants of a sample are computed with a recursive query
(`poresira.samples.lineage`), and the sample page shows the whole family tree.

## Rules enforced by the database

- **Identity is read-only.** Identifier, project, kind, collection event and parent cannot change.
- **A reason is required** to change the concentration, volume, status, barcode, storage
  location or position. A trigger rejects the update unless a reason is in the audit context;
  the web forms and `with_reason(...)` supply it, and it is written to the audit trail.
- **One sample per position.** A storage position holds at most one active sample, and a
  position requires a location. Positions are normalised (`a1` becomes `A1`) and checked
  against the grid of the box (at most 26 rows).
- **Unique labels and barcodes.** A label is unique per project and kind among active samples;
  a barcode is unique.
- **QC readings are immutable.** A wrong reading is voided with a reason and a new one recorded.
- **Void, never delete.** A sample with active derivatives, a collection event with active samples and a
  storage location with active samples or sub-locations cannot be voided.

## Permissions

| Action | Who |
|---|---|
| See a sample, event or its history | members of its project, administrators |
| Register, edit, derive, move, record QC, void | project lead or contributor, administrator (never the guest role) |
| Create a storage location | administrator, platform manager |

Viewers and guests are read-only. A sample of a project you do not belong to answers 404, so
its existence is not revealed.

## Importing field samples

```bash
python manage.py import_field_samples samples.csv --project DEMO-SPBF --user alice --dry-run
```

Required column: `label`. Rows that share the same collection columns (`collected_on`,
`country`, `region`, `province`, `locality`, `site_name`, `latitude`, `longitude`, `collector`,
`event_notes`) share one new collection event. Optional sample columns: `host_taxon`,
`host_tax_id`, `host_variety`, `symptom_score`, `symptom_notes`, `barcode`,
`concentration_ng_ul`, `volume_ul`, `notes`. The import is all-or-nothing: if any row is wrong,
nothing is written and every problem is listed with its line number. `--dry-run` validates and
rolls back.

## Demonstration data

```bash
docker compose exec web python manage.py load_demo_data --allow-production --password '...'
```

Builds the project `DEMO-SPBF` from two published studies of sweetpotato virus diseases in
Burkina Faso: the 66 BioSamples of BioProject PRJNA1460897 and Table 3 of Tibiri et al. 2020
(*Plant Pathology*, CC BY 4.0). It creates about 290 samples with their lineage, a storage tree
and four accounts (`demo_platform`, `demo_lead`, `demo_technician`, `demo_viewer`).
`--synthetic-volume N` adds the project `DEMO-SYNTHETIC` with random samples (seed 2026) to
test performance. The command refuses to run when `DEBUG` is off unless `--allow-production`
is given, because the accounts it creates use a known password.

What is real and what is illustrative is documented in
`poresira/samples/demo/data/README.md`. In short: names, places, dates and coordinates are as
published; storage positions, the storage tree and accounts are illustrative; no instrument
readings are invented.
