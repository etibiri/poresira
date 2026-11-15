# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bulk entry of one library from CSV rows, all or nothing."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from poresira.core.models import Project
from poresira.samples.models import Sample

from . import services
from .models import Library, LibraryKit, LibraryMember

COLUMNS = ("sample", "barcode", "input_ng", "notes")
FIRST_DATA_LINE = 2  # line 1 is the header


class LibraryImportError(Exception):
    """The import was refused; nothing was saved. ``problems`` lists what to fix."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class ImportReport:
    library: Library
    members: list[LibraryMember] = field(default_factory=list)


def _find_sample(project: Project, text: str) -> Sample:
    """A sample is named by its identifier, or by its label when that is unambiguous."""
    candidates = Sample.objects.active().filter(project=project)
    matches = list(candidates.filter(identifier=text)) or list(candidates.filter(label=text))
    if not matches:
        raise ValueError(f"no sample {text!r} in this project")
    if len(matches) > 1:
        raise ValueError(f"{text!r} names several samples; use the identifier")
    return matches[0]


def _describe(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(error.messages)
    return str(error)


@transaction.atomic
def import_library(
    *,
    user,
    project: Project,
    kit: LibraryKit,
    rows: Iterable[Mapping[str, str]],
    seal: bool = False,
    **library_attributes,
) -> ImportReport:
    """Create a library and put each row's sample in it under its barcode."""
    rows = list(rows)
    unknown = {column for row in rows for column in row} - set(COLUMNS)
    if unknown:
        raise LibraryImportError([f"unknown columns: {sorted(unknown)}"])
    if not rows:
        raise LibraryImportError(["the file has no rows"])

    problems, parsed = [], []
    for line_number, row in enumerate(rows, start=FIRST_DATA_LINE):
        values = {column: (row.get(column) or "").strip() for column in COLUMNS}
        try:
            member = {"sample": _find_sample(project, values["sample"])}
            if values["barcode"]:
                member["barcode"] = values["barcode"]
            if values["input_ng"]:
                try:
                    member["input_ng"] = Decimal(values["input_ng"])
                except InvalidOperation as error:
                    raise ValueError(f"input_ng: {values['input_ng']!r} is not a number") from error
            if values["notes"]:
                member["notes"] = values["notes"]
            parsed.append(member)
        except (ValidationError, ValueError) as error:
            problems.append(f"line {line_number}: {_describe(error)}")
    if problems:
        raise LibraryImportError(problems)

    try:
        library = services.create_library(user=user, project=project, kit=kit, **library_attributes)
    except ValidationError as error:
        raise LibraryImportError([_describe(error)]) from error
    report = ImportReport(library=library)
    for line_number, member in enumerate(parsed, start=FIRST_DATA_LINE):
        try:
            report.members.append(services.add_member(user=user, library=library, **member))
        except (ValidationError, services.SequencingServiceError) as error:
            problems.append(f"line {line_number}: {_describe(error)}")
    if problems:
        raise LibraryImportError(problems)
    if seal:
        try:
            services.seal_library(user=user, library=library)
        except (ValidationError, services.SequencingServiceError) as error:
            raise LibraryImportError([_describe(error)]) from error
    return report
