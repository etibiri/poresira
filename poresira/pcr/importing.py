# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bulk entry of one PCR run from CSV rows, all or nothing."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from poresira.core.models import Project
from poresira.samples.models import Sample

from . import services
from .models import Assay, Container, PcrReaction, PcrRun, Result, Role

COLUMNS = (
    "sample",
    "position",
    "role",
    "result",
    "ct",
    "band_size_bp",
    "template_volume_ul",
    "notes",
)
FIRST_DATA_LINE = 2  # line 1 is the header


class PcrImportError(Exception):
    """The import was refused; nothing was saved. ``problems`` lists what to fix."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class ImportReport:
    run: PcrRun
    reactions: list[PcrReaction] = field(default_factory=list)


def _find_sample(project: Project, text: str) -> Sample:
    """A sample is named by its identifier, or by its label when that is unambiguous."""
    candidates = Sample.objects.active().filter(project=project)
    matches = list(candidates.filter(identifier=text)) or list(candidates.filter(label=text))
    if not matches:
        raise ValueError(f"no sample {text!r} in this project")
    if len(matches) > 1:
        raise ValueError(f"{text!r} names several samples; use the identifier")
    return matches[0]


def _decimal(column: str, text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"{column}: {text!r} is not a number") from error


def _parse(project: Project, row: Mapping[str, str]) -> dict:
    values = {column: (row.get(column) or "").strip() for column in COLUMNS}
    role = values["role"] or Role.SAMPLE
    if role not in Role.values:
        raise ValueError(f"role: {role!r} is not one of {', '.join(Role.values)}")
    result = values["result"]
    if result and result not in (Result.POSITIVE, Result.NEGATIVE, Result.INCONCLUSIVE):
        raise ValueError(f"result: {result!r} is not positive, negative or inconclusive")
    reaction = {"position": values["position"], "role": role}
    if values["sample"]:
        reaction["sample"] = _find_sample(project, values["sample"])
    if values["template_volume_ul"]:
        reaction["template_volume_ul"] = _decimal(
            "template_volume_ul", values["template_volume_ul"]
        )
    if values["notes"]:
        reaction["notes"] = values["notes"]
    outcome = {"result": result}
    if values["ct"]:
        outcome["ct"] = _decimal("ct", values["ct"])
    if values["band_size_bp"]:
        outcome["band_size_bp"] = int(values["band_size_bp"])
    return {"reaction": reaction, "outcome": outcome}


def _describe(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(error.messages)
    return str(error)


@transaction.atomic
def import_pcr_run(
    *,
    user,
    project: Project,
    assay: Assay,
    rows: Iterable[Mapping[str, str]],
    container: str = Container.PLATE_96,
    tube_count: int | None = None,
    performed_on: date | None = None,
    reconstructed: bool = False,
    close: bool = False,
    **run_attributes,
) -> ImportReport:
    """Create a run with its reactions. If the rows carry results the run is marked performed
    (it needs ``performed_on`` unless ``reconstructed``) and, with ``close``, closed."""
    rows = list(rows)
    unknown = {column for row in rows for column in row} - set(COLUMNS)
    if unknown:
        raise PcrImportError([f"unknown columns: {sorted(unknown)}"])
    has_results = [bool((row.get("result") or "").strip()) for row in rows]
    if any(has_results) and not all(has_results):
        raise PcrImportError(["every row needs a result, or none of them"])

    problems, parsed = [], []
    for line_number, row in enumerate(rows, start=FIRST_DATA_LINE):
        try:
            parsed.append((line_number, _parse(project, row)))
        except (ValidationError, ValueError) as error:
            problems.append(f"line {line_number}: {_describe(error)}")
    if problems:
        raise PcrImportError(problems)

    try:
        run = services.plan_run(
            user=user,
            project=project,
            assay=assay,
            container=container,
            tube_count=tube_count,
            reconstructed=reconstructed,
            **run_attributes,
        )
    except ValidationError as error:
        raise PcrImportError([_describe(error)]) from error
    report = ImportReport(run=run)
    for line_number, item in parsed:
        try:
            report.reactions.append(services.add_reaction(user=user, run=run, **item["reaction"]))
        except (ValidationError, services.PcrServiceError) as error:
            problems.append(f"line {line_number}: {_describe(error)}")
    if problems:
        raise PcrImportError(problems)
    if rows and all(has_results):
        try:
            services.mark_performed(user=user, run=run, performed_on=performed_on)
            for (_line, item), reaction in zip(parsed, report.reactions, strict=True):
                services.record_result(user=user, reaction=reaction, **item["outcome"])
            if close:
                services.close_run(user=user, run=run)
        except (ValidationError, services.PcrServiceError) as error:
            raise PcrImportError([_describe(error)]) from error
    return report
