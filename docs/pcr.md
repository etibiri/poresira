<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# PCR

The `pcr` app records the primers, cycling programs and recipes of the laboratory, the PCR runs
made with them, and the result of every reaction. All tables are audited (see
[Architecture](architecture.md)).

## Domain model

```text
Primer ──┐
CyclingProgram ──< CyclingStep           Assay ──< PcrRun ──< PcrReaction >── Sample
MasterMix ──< MasterMixComponent ────────┘
```

| Model | Identifier | Purpose |
|---|---|---|
| `Primer` | none | Name, sequence (IUPAC codes allowed), direction (forward or reverse) |
| `CyclingProgram` | none | Initial steps, a number of cycles of the cycle steps, final steps |
| `MasterMix` | none | Reaction volume and the ingredients with their volumes |
| `Assay` | none | A forward and a reverse primer, a program, an optional master mix, reverse transcription or not, expected amplicon size |
| `PcrRun` | `WBF-T26-00001-5` | One go in the thermocycler, in a 96-well plate or in 1 to 96 PCR tubes |
| `PcrReaction` | none | One well or tube: a template or a control, and its result |

Primers, programs, master mixes and assays are shared by all projects and managed by
administrators and platform managers. Runs and reactions belong to a project.

## A run, step by step

1. **Planned.** Choose the assay and the container (plate or tubes). Add reactions: a position
   (`A1`, or a tube number; empty takes the next free one), a role (sample, positive control,
   no-template control, extraction blank) and, for a sample, the template and its volume.
2. **Performed.** Give the date, thermocycler and lots. The layout is now fixed.
3. **Results.** Enter positive, negative or inconclusive, with an optional Ct and the band size on
   the gel. A band within 10 % of the expected size is flagged by the service layer.
4. **Closed.** Every reaction needs a result. After that, a result changes only with a reason,
   and the run reopens only with a reason.

The run page draws the plate (A to H by 1 to 12) or the tubes, coloured by result.

## Validity of a run

A run is **valid** only if every positive control amplified and every no-template control and
extraction blank stayed negative; it is **invalid** if one failed; otherwise it is **not
assessed** (no controls, a control without result, or a run reconstructed from a publication).
PoreSira never concludes more than the controls allow.

## Rules enforced by the database

- Identity is read-only: primer sequence and direction, program cycles, assay design, run
  project, assay, container and tube count, reaction run and position.
- One active reaction per position of a run; a sample reaction needs a template; Ct, band size
  and template volume must be positive.
- A template belongs to the project of the run and cannot be a living plant (use an extract).
- A tube run has 1 to 96 tubes; a plate run has none.
- Changing a result of a closed run, or reopening a run, needs a reason.
- Void, never delete: a run with active reactions, an assay with active runs, a primer, program or
  master mix used by an active assay, or a sample with active reactions cannot be voided.

## Permissions

| Action | Who |
|---|---|
| See runs and reactions | members of the project, administrators |
| Plan, add reactions, enter results, close, reopen, void | project lead or contributor, administrator |
| Create primers, programs, master mixes, assays | administrator, platform manager (through the services) |

Primers, assays and programs are listed in the web interface but created through
`poresira.pcr.services` (or the demonstration loader); there is no creation form yet.

## Importing a run

```bash
python manage.py import_pcr_run run.csv --project DEMO-SPBF --assay "SPLCV CP PCR" \
    --user alice --tubes 49 --performed-on 2026-03-02 --close --dry-run
```

Columns: `sample` (identifier, or an unambiguous label in the project), `position`, `role`,
`result`, `ct`, `band_size_bp`, `template_volume_ul`, `notes`. Without `--tubes` the container is
a 96-well plate. Results must be given for all rows or none. `--reconstructed` marks a run taken
from a publication. The import is all-or-nothing and lists every problem with its line number.

## Demonstration data

`load_demo_data` adds six primers, three cycling programs and three assays from Tibiri et al.
2020 (SPFMV and SPCSV RT-PCR, SPLCV PCR with SPG1/SPG2), three reconstructed runs holding the 150
published results, and two runs holding the SPG1/SPG2 results of the 98 laboratory samples (53
positive). Dates, layouts and controls are not recorded in those sources, so the runs are marked
reconstructed and are never judged valid or invalid. Gel images are not stored yet: only band size
and notes are, until the `storage` app exists.
