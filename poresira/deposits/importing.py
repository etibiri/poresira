# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bulk entry of accessions that already exist in a public archive, all or nothing."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction

from poresira.core.models import Project
from poresira.samples.models import Sample, SampleKind

from . import services
from .models import PublicAccession

COLUMNS = ("sample", "kind", "accession", "released_on", "notes")
FIRST_DATA_LINE = 2  # line 1 is the header


class AccessionImportError(Exception):
    """The import was refused; nothing was saved. ``problems`` lists what to fix."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class ImportReport:
    created: list[PublicAccession] = field(default_factory=list)


def _biological_sample(project: Project, text: str) -> Sample:
    """A field sample or living plant, named by its identifier or by its label.

    A label can name a field sample and the plant grafted from it: the field sample is the one
    the archive's records describe, so it wins; a plant is chosen only when no field sample
    has the label."""
    candidates = Sample.objects.active().filter(project=project)
    by_kind = [
        candidates.filter(kind=SampleKind.FIELD_SAMPLE),
        candidates.filter(kind=SampleKind.LIVING_PLANT),
    ]
    matches: list[Sample] = []
    for pool in by_kind:
        matches = list(pool.filter(identifier=text)) or list(pool.filter(label=text))
        if matches:
            break
    if not matches:
        raise ValueError(f"no field sample or living plant {text!r} in this project")
    if len(matches) > 1:
        raise ValueError(f"{text!r} names several samples; use the identifier")
    return matches[0]


def _describe(error: Exception) -> str:
    return "; ".join(error.messages) if isinstance(error, ValidationError) else str(error)


@transaction.atomic
def import_public_accessions(
    *, user, project: Project, rows: Iterable[Mapping[str, str]]
) -> ImportReport:
    """Record each row's accession for its field sample (empty sample for a BioProject)."""
    rows = list(rows)
    unknown = {column for row in rows for column in row} - set(COLUMNS)
    if unknown:
        raise AccessionImportError([f"unknown columns: {sorted(unknown)}"])
    if not rows:
        raise AccessionImportError(["the file has no rows"])
    report, problems = ImportReport(), []
    for line_number, row in enumerate(rows, start=FIRST_DATA_LINE):
        values = {column: (row.get(column) or "").strip() for column in COLUMNS}
        try:
            released = date.fromisoformat(values["released_on"]) if values["released_on"] else None
            sample = _biological_sample(project, values["sample"]) if values["sample"] else None
            report.created.append(
                services.record_public_accession(
                    user=user,
                    project=project,
                    kind=values["kind"],
                    accession=values["accession"],
                    sample=sample,
                    released_on=released,
                    notes=values["notes"],
                )
            )
        except (ValidationError, ValueError) as error:
            problems.append(f"line {line_number}: {_describe(error)}")
    if problems:
        raise AccessionImportError(problems)
    return report
