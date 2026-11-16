# SPDX-License-Identifier: AGPL-3.0-or-later
"""The accessions the public archives already hold for the demonstration project: its NCBI
BioProject, the 66 BioSamples and the 66 SRA runs (released 2026-05-01), from the NCBI records
listed in ``data/``. Deposits read them so that nothing is registered a second time."""

import csv
from datetime import date
from pathlib import Path

from poresira.core.models import Project, User
from poresira.deposits.importing import import_public_accessions
from poresira.deposits.models import PublicAccession
from poresira.samples.models import Sample, SampleKind

DATA_DIR = Path(__file__).parent / "data"
BIOPROJECT = "PRJNA1460897"


def _rows() -> list[dict[str, str]]:
    rows = [{"sample": "", "kind": "bioproject", "accession": BIOPROJECT}]
    with (DATA_DIR / "biosamples_PRJNA1460897.csv").open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            rows.append(
                {
                    "sample": row["isolate"],
                    "kind": "biosample",
                    "accession": row["biosample_accession"],
                }
            )
    with (DATA_DIR / "sra_runs_PRJNA1460897.csv").open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            released = date.fromisoformat(row["release_date"]).isoformat()
            for kind, column in (("sra_run", "run"), ("sra_experiment", "experiment")):
                rows.append(
                    {
                        "sample": row["sample_name"],
                        "kind": kind,
                        "accession": row[column],
                        "released_on": released,
                    }
                )
    return rows


def load_public_accessions(users: dict[str, User], project: Project) -> int:
    """Record the accessions that are not recorded yet. Returns how many were added."""
    known = set(
        PublicAccession.objects.active().filter(project=project).values_list("accession", flat=True)
    )
    labels = set(
        Sample.objects.active()
        .filter(project=project, kind__in=[SampleKind.FIELD_SAMPLE, SampleKind.LIVING_PLANT])
        .values_list("label", flat=True)
    )  # a row naming an unknown sample is left out, not an error: the demo may be partial
    missing = [
        row
        for row in _rows()
        if row["accession"] not in known and (not row["sample"] or row["sample"] in labels)
    ]
    if not missing:
        return 0
    return len(
        import_public_accessions(
            user=users["demo_technician"], project=project, rows=missing
        ).created
    )
