# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bulk import of field samples from CSV rows, all or nothing."""

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction

from poresira.core.models import Project

from . import services
from .models import CollectionEvent, Sample

EVENT_COLUMNS = (
    "collected_on",
    "country",
    "region",
    "province",
    "locality",
    "site_name",
    "latitude",
    "longitude",
    "collector",
    "event_notes",
)
SAMPLE_COLUMNS = (
    "label",
    "host_taxon",
    "host_tax_id",
    "host_variety",
    "symptom_score",
    "symptom_notes",
    "barcode",
    "concentration_ng_ul",
    "volume_ul",
    "notes",
)
DATE_COLUMNS = {"collected_on"}
INTEGER_COLUMNS = {"host_tax_id", "symptom_score"}
DECIMAL_COLUMNS = {"latitude", "longitude", "concentration_ng_ul", "volume_ul"}
FIRST_DATA_LINE = 2  # line 1 is the header


class FieldSampleImportError(Exception):
    """The import was refused; nothing was saved. ``problems`` lists what to fix."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class ImportReport:
    events: list[CollectionEvent] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as csv_file:
        return list(csv.DictReader(csv_file))


def _convert(column: str, text: str):
    if column in DATE_COLUMNS:
        return date.fromisoformat(text)
    if column in INTEGER_COLUMNS:
        return int(text)
    if column in DECIMAL_COLUMNS:
        try:
            return Decimal(text)
        except InvalidOperation as error:
            raise ValueError(f"{column}: {text!r} is not a number") from error
    return text


def _split_row(row: Mapping[str, str]) -> tuple[dict, dict]:
    event_values, sample_values = {}, {}
    for column, text in row.items():
        text = (text or "").strip()
        if not text:
            continue
        target = event_values if column in EVENT_COLUMNS else sample_values
        target[column] = _convert(column, text)
    if "event_notes" in event_values:
        event_values["notes"] = event_values.pop("event_notes")
    return event_values, sample_values


def _describe(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(error.messages)
    return str(error)


@transaction.atomic
def import_field_samples(
    *, user, project: Project, rows: Iterable[Mapping[str, str]]
) -> ImportReport:
    """Create collection events and field samples; rows sharing event columns share an event."""
    rows = list(rows)
    known_columns = set(EVENT_COLUMNS) | set(SAMPLE_COLUMNS)
    unknown_columns = {column for row in rows for column in row} - known_columns
    if unknown_columns:
        raise FieldSampleImportError([f"unknown columns: {sorted(unknown_columns)}"])

    report, events_by_key, problems = ImportReport(), {}, []
    for line_number, row in enumerate(rows, start=FIRST_DATA_LINE):
        try:
            event_values, sample_values = _split_row(row)
            event_key = tuple(sorted(event_values.items()))
            if event_key not in events_by_key:
                events_by_key[event_key] = services.create_collection_event(
                    user=user, project=project, **event_values
                )
                report.events.append(events_by_key[event_key])
            report.samples.append(
                services.register_field_sample(
                    user=user, collection_event=events_by_key[event_key], **sample_values
                )
            )
        except (ValidationError, ValueError) as error:
            problems.append(f"line {line_number}: {_describe(error)}")
    if problems:
        raise FieldSampleImportError(problems)
    return report
