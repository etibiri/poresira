# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sequencing demo data: the kits, flow cells and instrument of the sweetpotato RCA runs.

Only what the laboratory recorded is loaded: the kits and flow cell types (Front Microbiol 2026,
Oxford Nanopore catalogue), the MinION Mk1C, the two MinION runs of November 2023 (from the
laboratory e-mail and its barcode sheet) and the Flongle run of 2022 (from the MinKNOW files).
"""

import csv
from datetime import date
from pathlib import Path

from django.db.models import Q

from poresira.core.models import Project, User
from poresira.samples.models import QcOutcome, Sample, SampleKind
from poresira.sequencing import services
from poresira.sequencing.models import (
    Chemistry,
    FlowCell,
    FlowCellType,
    Instrument,
    Library,
    LibraryKit,
    SequencingRun,
)

KITS = [
    ("SQK-NBD114.96", "Native Barcoding Kit 96 V14", Chemistry.NATIVE_BARCODING, 96),
    ("SQK-RBK114.96", "Rapid Barcoding Kit 96 V14", Chemistry.RAPID_BARCODING, 96),
    ("SQK-PCB114.24", "PCR-cDNA Barcoding Kit 24 V14", Chemistry.CDNA_PCR_BARCODING, 24),
    # Known only from the MinKNOW protocol name of the Flongle run; no barcodes were used.
    ("SQK-PSK004", "SQK-PSK004 (as named in the MinKNOW protocol)", Chemistry.OTHER, None),
]
# (code, name, pore, reusable). A Flongle is single-use; MinION and PromethION cells are washed
# and reloaded.
FLOW_CELL_TYPES = [
    ("FLO-MIN114", "MinION flow cell R10.4.1", "R10.4.1", True),
    ("FLO-FLG001", "Flongle flow cell R9.4.1", "R9.4.1", False),
    ("FLO-PRO114M", "PromethION flow cell R10.4.1", "R10.4.1", True),
]
INSTRUMENTS = [("MinION Mk1C MC-111618", "MinION Mk1C"), ("MinION Mk1B", "MinION Mk1B")]
DATA_FILE = Path(__file__).parent / "data" / "sequencing_barcodes.csv"
MINION_RUNS = {
    "FAT93267": {
        "name": "RCA products, MinION run 1",
        "run_id": "20231109_1221_mc-111618_fat93267_14bc03be",
        "notes": "Run directory /data/NAME_sequencing/RCA_MinION/ on the MinION Mk1C.",
    },
    "FAV02927": {
        "name": "RCA products, MinION run 2",
        "run_id": "",
        "notes": "Run directory /data/NAME_sequencing/RCA_MinION_2/ on the MinION Mk1C.",
    },
}
MINION_NOTES = (
    "Taken from the laboratory e-mail and barcode sheet. FAST5 converted to POD5, basecalled "
    "with Dorado v0.9.0, model dna_r10.4.1_e8.2_400bps_sup.cfg (as written in the e-mail). "
    "Reads per barcode are not recorded."
)


FLONGLE_RUN_ID = "20221104_1920_mc-111618_amf735_c77675e1"
FLONGLE_NOTES = (
    "Flongle AMF735 (adapter FA-06369) on the MinION Mk1C MC-111618, 4 to 6 November 2022; "
    "MinKNOW 21.11.7, acquisition ID 10f834811c6cc55747f2c2a6f43413289193c1b2. Not barcoded. "
    "Reads and bases are those of the MinKNOW throughput file (passed and failed). "
    "The protocol group 'Equiphi29' is kept as MinKNOW recorded it. The run was not barcoded "
    "(the thesis document gives this sample BC53; that barcode is not used here)."
)


def _product(project: Project, label: str) -> Sample:
    """The RCA product of a sample; with several attempts, the one whose gel passed."""
    candidates = Sample.objects.active().filter(
        Q(label=label) | Q(label__startswith=f"{label} ["),
        project=project,
        kind=SampleKind.AMPLIFICATION_PRODUCT,
    )
    if candidates.count() > 1:
        candidates = candidates.filter(qc_measurements__outcome=QcOutcome.PASSED)
    return candidates.get()


def _load_minion_runs(users: dict[str, User], project: Project, labels: dict[str, str]) -> None:
    """One library and one completed run per MinION flow cell, with each sample's barcode."""
    with DATA_FILE.open(newline="", encoding="utf-8") as file:
        sheet = [row for row in csv.DictReader(file) if row["barcode"]]
    user = users["demo_technician"]
    kit = LibraryKit.objects.active().get(code="SQK-NBD114.96")
    for flow_cell_id, spec in MINION_RUNS.items():
        rows = [row for row in sheet if row["flow_cell_id"] == flow_cell_id]
        if not rows or Library.objects.filter(project=project, name=spec["name"]).exists():
            continue
        library = services.create_library(
            user=user,
            project=project,
            kit=kit,
            name=spec["name"],
            reconstructed=True,
            notes="Preparation date and amounts are not recorded.",
        )
        for row in rows:
            services.add_member(
                user=user,
                library=library,
                sample=_product(project, labels[row["sample_code"]]),
                barcode=row["barcode"],
            )
        services.seal_library(user=user, library=library)
        run = services.plan_run(
            user=user,
            library=library,
            flow_cell=FlowCell.objects.active().get(flow_cell_id=flow_cell_id),
            instrument=Instrument.objects.active().get(name="MinION Mk1C MC-111618"),
            reconstructed=True,
        )
        services.start_run(
            user=user,
            run=run,
            started_on=date.fromisoformat(rows[0]["sequenced_on"]),
            run_id=spec["run_id"],
        )
        services.complete_run(
            user=user,
            run=run,
            basecaller="Dorado",
            basecaller_version="0.9.0",
            basecall_model="dna_r10.4.1_e8.2_400bps_sup.cfg",
            notes=f"{spec['notes']} {MINION_NOTES}",
        )


def _load_flongle_run(users: dict[str, User], project: Project) -> None:
    """BFA316 on a Flongle (the thesis work of a master's student), from the MinKNOW files."""
    if SequencingRun.objects.filter(project=project, run_id=FLONGLE_RUN_ID).exists():
        return
    sample = (
        Sample.objects.active()
        .filter(project=project, kind=SampleKind.AMPLIFICATION_PRODUCT, label="Tie_BFA316")
        .first()
    )
    if sample is None:
        return
    user = users["demo_technician"]
    library = services.create_library(
        user=user,
        project=project,
        kit=LibraryKit.objects.active().get(code="SQK-PSK004"),
        name="BFA316 Flongle",
        reconstructed=True,
        notes="Preparation date and amounts are not recorded.",
    )
    member = services.add_member(user=user, library=library, sample=sample)
    services.seal_library(user=user, library=library)
    flow_cell = FlowCell.objects.active().get(flow_cell_id="AMF735")
    run = services.plan_run(
        user=user,
        library=library,
        flow_cell=flow_cell,
        instrument=Instrument.objects.active().get(name="MinION Mk1C MC-111618"),
    )
    services.start_run(
        user=user,
        run=run,
        started_on=date(2022, 11, 4),
        run_id=FLONGLE_RUN_ID,
        acquisition_software="MinKNOW 21.11.7",
    )
    services.record_result(user=user, run=run, member=member, reads=530760, bases=462685503)
    services.complete_run(
        user=user,
        run=run,
        basecaller="Guppy",
        basecaller_version="5.1.13",
        total_reads=530760,
        total_bases=462685503,
        notes=FLONGLE_NOTES,
    )


def load_sequencing_demo(
    users: dict[str, User], project: Project | None = None, labels: dict[str, str] | None = None
) -> None:
    """Create the reference data, the flow cells and the runs. Safe to repeat.

    ``labels`` maps each laboratory sample code to the label of its sample."""
    platform = users["demo_platform"]
    for code, name, chemistry, barcodes in KITS:
        if not LibraryKit.objects.active().filter(code=code).exists():
            services.create_library_kit(
                user=platform, code=code, name=name, chemistry=chemistry, barcode_count=barcodes
            )
    for code, name, pore, reusable in FLOW_CELL_TYPES:
        if not FlowCellType.objects.active().filter(code=code).exists():
            services.create_flow_cell_type(
                user=platform, code=code, name=name, pore=pore, reusable=reusable
            )
    for name, model in INSTRUMENTS:
        if not Instrument.objects.active().filter(name=name).exists():
            services.create_instrument(user=platform, name=name, model=model)
    flow_cells = [(flow_cell, "FLO-MIN114") for flow_cell in MINION_RUNS] + [
        ("AMF735", "FLO-FLG001")
    ]
    for flow_cell_id, type_code in flow_cells:
        if not FlowCell.objects.active().filter(flow_cell_id=flow_cell_id).exists():
            services.register_flow_cell(
                user=users["demo_technician"],
                flow_cell_id=flow_cell_id,
                type=FlowCellType.objects.active().get(code=type_code),
            )
    if project is not None:
        _load_minion_runs(users, project, labels or {})
        _load_flongle_run(users, project)
