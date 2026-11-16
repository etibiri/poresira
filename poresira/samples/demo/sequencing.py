# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sequencing demo data: the kits, flow cells and instrument of the sweetpotato RCA runs.

Only what the laboratory recorded is loaded: the kits and flow cell types (Front Microbiol 2026,
Oxford Nanopore catalogue), the MinION Mk1C and the two flow cells of the November 2023 runs.
The barcode of each sample is not in the records yet: load it with ``import_library`` once the
laboratory sheet is found, then plan the runs (see ``RUNS``).
"""

from datetime import date

from poresira.core.models import Project, User
from poresira.samples.models import Sample, SampleKind
from poresira.sequencing import services
from poresira.sequencing.models import (
    Chemistry,
    FlowCell,
    FlowCellType,
    Instrument,
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
# The two runs of the RCA products (laboratory e-mail): flow cell, kit, barcodes, date, run ID.
RUNS = [
    {
        "flow_cell": "FAT93267",
        "barcodes": "BC01-BC24",
        "date": "2023-11-09",
        "run_id": "20231109_1221_MC-111618_FAT93267_14bc03be",
    },
    {"flow_cell": "FAV02927", "barcodes": "BC25-BC52", "date": "2023-11-11", "run_id": ""},
]


FLONGLE_RUN_ID = "20221104_1920_mc-111618_amf735_c77675e1"
FLONGLE_NOTES = (
    "Flongle AMF735 (adapter FA-06369) on the MinION Mk1C MC-111618, 4 to 6 November 2022; "
    "MinKNOW 21.11.7, acquisition ID 10f834811c6cc55747f2c2a6f43413289193c1b2. Not barcoded. "
    "Reads and bases are those of the MinKNOW throughput file (passed and failed). "
    "MinKNOW names the protocol group 'Equiphi29', while the thesis sheet records only "
    "TempliPhi for this sample; the thesis document gives it barcode BC53."
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


def load_sequencing_demo(users: dict[str, User], project: Project | None = None) -> None:
    """Create the reference data, the flow cells and the Flongle run. Safe to repeat."""
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
    flow_cells = [(run["flow_cell"], "FLO-MIN114") for run in RUNS] + [("AMF735", "FLO-FLG001")]
    for flow_cell_id, type_code in flow_cells:
        if not FlowCell.objects.active().filter(flow_cell_id=flow_cell_id).exists():
            services.register_flow_cell(
                user=users["demo_technician"],
                flow_cell_id=flow_cell_id,
                type=FlowCellType.objects.active().get(code=type_code),
            )
    if project is not None:
        _load_flongle_run(users, project)
