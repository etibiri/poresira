# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every change to samples goes through here: permissions, rules, reasons and audit context.

Functions run in one transaction. Changing a critical field needs a ``reason``; the database
enforces it too (see ``poresira.core.pgtriggers.RequireReason``).
"""

import re
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from poresira.core.models import Project
from poresira.core.permissions import can_edit_project
from poresira.core.reason import acting_as, with_reason

from .models import (
    CollectionEvent,
    QcMeasurement,
    Sample,
    SampleKind,
    SampleStatus,
    StorageLocation,
)
from .models.sample import OCCUPYING_STATUSES
from .permissions import can_manage_storage

COLLECTION_ATTRIBUTES = {
    "collected_on",
    "country",
    "region",
    "province",
    "locality",
    "site_name",
    "latitude",
    "longitude",
    "collector",
    "notes",
}
DESCRIPTIVE_SAMPLE_ATTRIBUTES = {
    "label",
    "host_taxon",
    "host_tax_id",
    "host_variety",
    "symptom_score",
    "symptom_notes",
    "notes",
}
SAMPLE_CREATION_ATTRIBUTES = DESCRIPTIVE_SAMPLE_ATTRIBUTES | {
    "barcode",
    "concentration_ng_ul",
    "volume_ul",
}
INHERITED_HOST_ATTRIBUTES = ("host_taxon", "host_tax_id", "host_variety")
QUANTITY_ATTRIBUTES = {"concentration_ng_ul", "volume_ul"}
CRITICAL_CHANGE_ATTRIBUTES = QUANTITY_ATTRIBUTES | {"status", "barcode"}
QC_ATTRIBUTES = {
    "measured_at",
    "concentration_ng_ul",
    "a260_280",
    "a260_230",
    "integrity_number",
    "outcome",
    "notes",
}
STORAGE_ATTRIBUTES = {"nominal_temperature_c", "grid_rows", "grid_columns", "notes"}

ALLOWED_DERIVATIONS = {
    SampleKind.FIELD_SAMPLE: {
        SampleKind.LIVING_PLANT,
        SampleKind.DNA_EXTRACT,
        SampleKind.RNA_EXTRACT,
    },
    SampleKind.LIVING_PLANT: {
        SampleKind.LIVING_PLANT,
        SampleKind.DNA_EXTRACT,
        SampleKind.RNA_EXTRACT,
    },
    SampleKind.DNA_EXTRACT: {SampleKind.DNA_EXTRACT, SampleKind.AMPLIFICATION_PRODUCT},
    SampleKind.RNA_EXTRACT: {SampleKind.RNA_EXTRACT},
    SampleKind.AMPLIFICATION_PRODUCT: {SampleKind.AMPLIFICATION_PRODUCT},
}
MAX_GRID_ROWS = 26  # rows are single letters, A to Z
POSITION_PATTERN = re.compile(r"^([A-Z])0*(\d{1,3})$")
OCCUPIED_POSITION_CONSTRAINT = "one_active_sample_per_storage_position"


class SampleServiceError(Exception):
    """A rule of the sample module was not respected."""


class InvalidPositionError(SampleServiceError):
    pass


class PositionOccupiedError(SampleServiceError):
    pass


class InvalidDerivationError(SampleServiceError):
    pass


class RecordInUseError(SampleServiceError):
    """Raised when voiding a record that active records still depend on."""


def _require_project_editor(user, project: Project) -> None:
    if not can_edit_project(user, project):
        raise PermissionDenied


def _require_storage_manager(user) -> None:
    if not can_manage_storage(user):
        raise PermissionDenied


def _reject_unknown(attributes: dict, allowed: set[str]) -> None:
    if unknown := set(attributes) - allowed:
        raise TypeError(f"Unexpected attributes: {sorted(unknown)}")


def _save_new(instance) -> None:
    instance.full_clean(exclude=["identifier"])
    instance.save()


@transaction.atomic
def create_collection_event(*, user, project: Project, **attributes) -> CollectionEvent:
    _require_project_editor(user, project)
    _reject_unknown(attributes, COLLECTION_ATTRIBUTES)
    event = CollectionEvent(project=project, **attributes)
    with acting_as(user):
        _save_new(event)
    return event


@transaction.atomic
def edit_collection_event(*, user, event: CollectionEvent, **attributes) -> CollectionEvent:
    _require_project_editor(user, event.project)
    _reject_unknown(attributes, COLLECTION_ATTRIBUTES)
    for name, value in attributes.items():
        setattr(event, name, value)
    event.full_clean()
    with acting_as(user):
        event.save()
    return event


@transaction.atomic
def register_field_sample(*, user, collection_event: CollectionEvent, **attributes) -> Sample:
    _require_project_editor(user, collection_event.project)
    _reject_unknown(attributes, SAMPLE_CREATION_ATTRIBUTES)
    sample = Sample(
        project=collection_event.project,
        collection_event=collection_event,
        kind=SampleKind.FIELD_SAMPLE,
        **attributes,
    )
    with acting_as(user):
        _save_new(sample)
    return sample


@transaction.atomic
def derive_sample(*, user, parent: Sample, kind: str, **attributes) -> Sample:
    """Create an extract, plant or amplification product from ``parent``; host data is copied."""
    _require_project_editor(user, parent.project)
    _reject_unknown(attributes, SAMPLE_CREATION_ATTRIBUTES)
    if parent.is_voided:
        raise InvalidDerivationError(_("A voided sample cannot be used as a parent."))
    if kind not in ALLOWED_DERIVATIONS[parent.kind]:
        raise InvalidDerivationError(
            _("A %(parent_kind)s cannot give a %(kind)s.")
            % {"parent_kind": SampleKind(parent.kind).label, "kind": SampleKind(kind).label}
        )
    inherited = {name: getattr(parent, name) for name in INHERITED_HOST_ATTRIBUTES}
    derivative = Sample(
        project=parent.project,
        collection_event=parent.collection_event,
        parent=parent,
        kind=kind,
        **{**inherited, **attributes},
    )
    with acting_as(user):
        _save_new(derivative)
    return derivative


@transaction.atomic
def edit_sample(*, user, sample: Sample, **attributes) -> Sample:
    """Change descriptive fields. Critical fields have their own functions and need a reason."""
    _require_project_editor(user, sample.project)
    _reject_unknown(attributes, DESCRIPTIVE_SAMPLE_ATTRIBUTES)
    for name, value in attributes.items():
        setattr(sample, name, value)
    sample.full_clean()
    with acting_as(user):
        sample.save()
    return sample


def normalize_position(location: StorageLocation | None, position: str) -> str:
    """Validate a grid position such as ``a03`` against the box and return it as ``A3``."""
    position = position.strip().upper()
    if not position:
        return ""
    if location is None:
        raise InvalidPositionError(_("A position needs a storage location."))
    if not (location.grid_rows and location.grid_columns):
        raise InvalidPositionError(
            _("%(location)s has no grid of positions.") % {"location": location.name}
        )
    match = POSITION_PATTERN.match(position)
    if match is None:
        raise InvalidPositionError(
            _("%(position)s is not a position such as A1.") % {"position": position}
        )
    row, column = ord(match.group(1)) - ord("A") + 1, int(match.group(2))
    if not (1 <= row <= location.grid_rows and 1 <= column <= location.grid_columns):
        raise InvalidPositionError(
            _("%(position)s is outside the %(rows)s x %(columns)s grid of %(location)s.")
            % {
                "position": position,
                "rows": location.grid_rows,
                "columns": location.grid_columns,
                "location": location.name,
            }
        )
    return f"{match.group(1)}{column}"


def _occupant(location: StorageLocation, position: str, excluding: Sample) -> Sample | None:
    return (
        Sample.objects.active()
        .filter(storage_location=location, position=position, status__in=OCCUPYING_STATUSES)
        .exclude(pk=excluding.pk)
        .first()
    )


@transaction.atomic
def move_sample(
    *, user, sample: Sample, location: StorageLocation | None, position: str = "", reason: str
) -> Sample:
    """Put a sample in a location (and grid position), or take it out with ``location=None``."""
    _require_project_editor(user, sample.project)
    if sample.is_voided:
        raise SampleServiceError(_("A voided sample cannot be moved."))
    if location is not None and location.is_voided:
        raise InvalidPositionError(_("A voided storage location cannot be used."))
    position = normalize_position(location, position)
    if (sample.storage_location_id, sample.position) == (location and location.pk, position):
        return sample
    if position and (occupant := _occupant(location, position, sample)):
        raise PositionOccupiedError(
            _("Position %(position)s of %(location)s already holds %(sample)s.")
            % {"position": position, "location": location.name, "sample": occupant.identifier}
        )

    sample.storage_location, sample.position = location, position
    if location is not None and sample.status == SampleStatus.REGISTERED:
        sample.status = SampleStatus.STORED
    try:
        with acting_as(user), with_reason(reason), transaction.atomic():
            sample.save()
    except IntegrityError as error:
        if OCCUPIED_POSITION_CONSTRAINT in str(error):
            raise PositionOccupiedError(
                _("Position %(position)s of %(location)s was just taken.")
                % {"position": position, "location": location.name}
            ) from error
        raise
    return sample


@transaction.atomic
def change_sample(*, user, sample: Sample, reason: str, **changes) -> Sample:
    """Change status, barcode, concentration or volume. The reason goes to the audit log."""
    _require_project_editor(user, sample.project)
    _reject_unknown(changes, CRITICAL_CHANGE_ATTRIBUTES)
    if sample.is_voided:
        raise SampleServiceError(_("A voided sample cannot be changed."))
    for name, value in changes.items():
        setattr(sample, name, value)
    sample.full_clean()
    with acting_as(user), with_reason(reason):
        sample.save()
    return sample


@transaction.atomic
def record_qc(
    *, user, sample: Sample, method: str, apply_to_sample: bool = False, **values
) -> QcMeasurement:
    """Log an instrument reading. ``apply_to_sample`` also sets the sample's concentration."""
    _require_project_editor(user, sample.project)
    _reject_unknown(values, QC_ATTRIBUTES)
    measurement = QcMeasurement(sample=sample, method=method, measured_by=user, **values)
    with acting_as(user):
        _save_new(measurement)
    concentration = values.get("concentration_ng_ul")
    if apply_to_sample and concentration is not None:
        change_sample(
            user=user,
            sample=sample,
            concentration_ng_ul=Decimal(concentration),
            reason=_("Concentration from %(method)s measurement of %(date)s")
            % {"method": measurement.get_method_display(), "date": measurement.measured_at.date()},
        )
    return measurement


@transaction.atomic
def create_storage_location(
    *, user, name: str, kind: str, parent: StorageLocation | None = None, **attributes
) -> StorageLocation:
    _require_storage_manager(user)
    _reject_unknown(attributes, STORAGE_ATTRIBUTES)
    if (attributes.get("grid_rows") or 0) > MAX_GRID_ROWS:
        raise ValidationError({"grid_rows": _("A grid has at most 26 rows (A to Z).")})
    location = StorageLocation(name=name, kind=kind, parent=parent, **attributes)
    with acting_as(user):
        location.full_clean()
        location.save()
    return location


def _project_of(record) -> Project:
    return record.sample.project if isinstance(record, QcMeasurement) else record.project


def _guard_voiding(record) -> None:
    if isinstance(record, Sample):
        if record.children.filter(voided_at__isnull=True).exists():
            raise RecordInUseError(_("Void the derivatives of this sample first."))
    elif isinstance(record, CollectionEvent):
        if record.samples.filter(voided_at__isnull=True).exists():
            raise RecordInUseError(_("Void the samples of this collection event first."))
    elif isinstance(record, StorageLocation) and (
        record.children.filter(voided_at__isnull=True).exists()
        or record.samples.filter(voided_at__isnull=True).exists()
    ):
        raise RecordInUseError(_("This location still contains active locations or samples."))


@transaction.atomic
def void_record(*, user, record, reason: str):
    """Void a sample, collection event, storage location or QC measurement."""
    if isinstance(record, StorageLocation):
        _require_storage_manager(user)
    else:
        _require_project_editor(
            user, record.project if hasattr(record, "project") else record.sample.project
        )
    _guard_voiding(record)
    with acting_as(user):
        record.void(user=user, reason=reason)
    return record
