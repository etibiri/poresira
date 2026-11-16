# SPDX-License-Identifier: AGPL-3.0-or-later
"""A fictitious data folder for the demonstration project, so the storage screens have files.

Every file is a small placeholder whose first line says it is fictitious. None is real signal,
real reads or a real gel; they only have the names and places such files would have, and they are
registered like any other file.
"""

import gzip
import random
from dataclasses import dataclass
from pathlib import Path

from poresira.core.models import User
from poresira.pcr.models import PcrRun
from poresira.sequencing.models import SequencingRun
from poresira.storage import services
from poresira.storage.importing import register_directory
from poresira.storage.models import BackendKind, DataFile, StorageBackend

BACKEND_NAME = "Demo disk"
NOTICE = "FICTITIOUS DEMO FILE - not real data"
# flow cell ID -> (folder of the run on the disk, number of barcodes)
RUN_FOLDERS = {
    "FAT93267": ("runs/RCA_MinION/20231109_1221_MC-111618_FAT93267_14bc03be", 24),
    "FAV02927": ("runs/RCA_MinION_2/20231111_0930_MC-111618_FAV02927_0f3a91c2", 28),
    "AMF735": ("runs/20221104_1920_MC-111618_AMF735_c77675e1", 0),
}
GEL_COUNT = 8  # Gel1 to Gel4 for the first SPG1/SPG2 run, Gel5 to Gel8 for the second


@dataclass
class DemoStorage:
    root: Path
    written: int = 0
    registered: int = 0


def _write(storage: DemoStorage, relative: str, content: bytes) -> None:
    path = storage.root / relative
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    storage.written += 1


def _filler(relative: str, size: int) -> bytes:
    """Deterministic bytes, so a file looks the same each time the folder is made."""
    generator = random.Random(relative)  # noqa: S311 - not security related
    return NOTICE.encode() + b"\n" + generator.randbytes(size)


def _fastq(relative: str, reads: int = 6) -> bytes:
    """A few random reads in valid FASTQ, compressed; the read names say they are fictitious."""
    generator = random.Random(relative)  # noqa: S311
    lines = []
    for number in range(reads):
        sequence = "".join(generator.choice("ACGT") for _ in range(120))
        lines += [f"@fictitious_demo_read_{number}", sequence, "+", "I" * len(sequence)]
    return gzip.compress(("\n".join(lines) + "\n").encode(), mtime=0)


def _run_files(storage: DemoStorage, folder: str, barcodes: int) -> None:
    for number in range(2):
        _write(
            storage, f"{folder}/pod5/demo_{number}.pod5", _filler(f"{folder}/pod5/{number}", 32_768)
        )
    _write(storage, f"{folder}/bam_pass/demo_0.bam", _filler(f"{folder}/bam", 16_384))
    for barcode in range(1, barcodes + 1):
        name = f"{folder}/fastq_pass/barcode{barcode:02d}/demo_0.fastq.gz"
        _write(storage, name, _fastq(name))
    if not barcodes:
        name = f"{folder}/fastq_pass/demo_0.fastq.gz"
        _write(storage, name, _fastq(name))
    for report in ("report.md", "final_summary.txt", "sequencing_summary.txt"):
        text = f"{NOTICE}\nPlaceholder for {report} of {folder}.\n"
        _write(storage, f"{folder}/{report}", text.encode())


def create_demo_storage(root: Path, *, platform: User, technician: User) -> DemoStorage:
    """Write the fictitious folder under ``root``, declare it as a backend and register what it
    holds for the demonstration runs and PCR runs. Safe to repeat."""
    root = root.resolve()
    storage = DemoStorage(root=root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "computing").mkdir(exist_ok=True)  # where compute jobs write their results
    for folder, barcodes in RUN_FOLDERS.values():
        _run_files(storage, folder, barcodes)
    for number in range(1, GEL_COUNT + 1):
        _write(storage, f"gels/Gel{number}.jpg", b"\xff\xd8\xff" + _filler(f"gel{number}", 8_192))

    backend = StorageBackend.objects.active().filter(name=BACKEND_NAME).first()
    backend = backend or services.create_backend(
        user=platform, name=BACKEND_NAME, kind=BackendKind.LOCAL, root=str(root)
    )
    for flow_cell_id, (folder, _barcodes) in RUN_FOLDERS.items():
        run = SequencingRun.objects.filter(flow_cell__flow_cell_id=flow_cell_id).first()
        if run is None:
            continue
        report = register_directory(
            user=technician,
            project=run.project,
            backend=backend,
            directory=root / folder,
            link={"sequencing_run": run},
        )
        storage.registered += len(report.registered)
    pcr_runs = PcrRun.objects.filter(notes__startswith="Sweepovirus detection").order_by(
        "created_at"
    )
    known = set(DataFile.objects.active().filter(backend=backend).values_list("path", flat=True))
    for index, pcr_run in enumerate(pcr_runs[:2]):
        for number in range(index * 4 + 1, index * 4 + 5):
            path = f"gels/Gel{number}.jpg"
            if path in known:
                continue
            services.register_file(
                user=technician,
                project=pcr_run.project,
                backend=backend,
                path=path,
                pcr_run=pcr_run,
                notes="Fictitious demonstration image.",
            )
            storage.registered += 1
    return storage
