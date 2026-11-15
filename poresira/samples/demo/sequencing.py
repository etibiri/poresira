# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sequencing demo data: the kits, flow cells and instrument of the sweetpotato RCA runs.

Only what the laboratory recorded is loaded: the kits and flow cell types (Front Microbiol 2026,
Oxford Nanopore catalogue), the MinION Mk1C and the two flow cells of the November 2023 runs.
The barcode of each sample is not in the records yet: load it with ``import_library`` once the
laboratory sheet is found, then plan the runs (see ``RUNS``).
"""

from poresira.core.models import User
from poresira.sequencing import services
from poresira.sequencing.models import (
    Chemistry,
    FlowCell,
    FlowCellType,
    Instrument,
    LibraryKit,
)

KITS = [
    ("SQK-NBD114.96", "Native Barcoding Kit 96 V14", Chemistry.NATIVE_BARCODING, 96),
    ("SQK-RBK114.96", "Rapid Barcoding Kit 96 V14", Chemistry.RAPID_BARCODING, 96),
    ("SQK-PCB114.24", "PCR-cDNA Barcoding Kit 24 V14", Chemistry.CDNA_PCR_BARCODING, 24),
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


def load_sequencing_demo(users: dict[str, User]) -> None:
    """Create the reference data and the two flow cells. Safe to repeat."""
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
    cell_type = FlowCellType.objects.active().get(code="FLO-MIN114")
    for run in RUNS:
        if not FlowCell.objects.active().filter(flow_cell_id=run["flow_cell"]).exists():
            services.register_flow_cell(
                user=users["demo_technician"], flow_cell_id=run["flow_cell"], type=cell_type
            )
