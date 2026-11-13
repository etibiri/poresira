# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every change to sequencing records goes through here: permissions, rules, reasons and audit.

Functions run in one transaction. Once a run is completed, a result changes only with a reason;
the database enforces it too. Platform-specific rules come from ``adapters``.
"""

from contextlib import nullcontext
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils.translation import gettext as _

from poresira.core.models import Project
from poresira.core.permissions import can_edit_project
from poresira.core.reason import acting_as, with_reason
from poresira.samples.models import Sample, SampleStatus

from .adapters import adapter_for
from .models import (
    BarcodeResult,
    FlowCell,
    FlowCellType,
    Instrument,
    Library,
    LibraryKit,
    LibraryMember,
    LibraryStatus,
    RunStatus,
    SequencingRun,
)
from .permissions import can_manage_reference_data, can_register_flow_cells

LIBRARY_ATTRIBUTES = {"name", "prepared_on", "prepared_by", "reconstructed", "notes"}
MEMBER_ATTRIBUTES = {"input_ng", "notes"}
RUN_ATTRIBUTES = {
    "instrument",
    "run_id",
    "started_on",
    "performed_by",
    "acquisition_software",
    "basecaller",
    "basecaller_version",
    "basecall_model",
    "total_reads",
    "total_bases",
    "reconstructed",
    "notes",
}
BASECALLING_ATTRIBUTES = {"basecaller", "basecaller_version", "basecall_model"}
RESULT_ATTRIBUTES = {"bases", "n50", "mean_quality", "notes"}
UNUSABLE_STATUSES = {SampleStatus.CONSUMED, SampleStatus.DISCARDED}


class SequencingServiceError(Exception):
    """A rule of the sequencing module was not respected."""


class LibraryStateError(SequencingServiceError):
    """The library is not in a state that allows this change."""


class RunStateError(SequencingServiceError):
    """The run is not in a state that allows this change."""


class RecordInUseError(SequencingServiceError):
    """Raised when voiding a record that active records still depend on."""


def _require_editor(user, project: Project) -> None:
    if not can_edit_project(user, project):
        raise PermissionDenied


def _require_reference_manager(user) -> None:
    if not can_manage_reference_data(user):
        raise PermissionDenied


def _reject_unknown(attributes: dict, allowed: set[str]) -> None:
    if unknown := set(attributes) - allowed:
        raise TypeError(f"Unexpected attributes: {sorted(unknown)}")


def _save_new(instance) -> None:
    instance.full_clean(exclude=["identifier"])
    instance.save()


def _require_library_state(library: Library, status: str) -> None:
    if library.is_voided:
        raise LibraryStateError(_("A voided library cannot be changed."))
    if library.status != status:
        raise LibraryStateError(
            _("This library is %(status)s: the change is not possible.")
            % {"status": LibraryStatus(library.status).label.lower()}
        )


def _require_run_state(run: SequencingRun, *allowed: str) -> None:
    if run.is_voided:
        raise RunStateError(_("A voided run cannot be changed."))
    if run.status not in allowed:
        raise RunStateError(
            _("This run is %(status)s: the change is not possible.")
            % {"status": RunStatus(run.status).label.lower()}
        )


# --- Reference data ---------------------------------------------------------------------------


@transaction.atomic
def create_library_kit(
    *, user, code: str, name: str, chemistry: str, barcode_count: int | None = None, **attributes
) -> LibraryKit:
    _require_reference_manager(user)
    _reject_unknown(attributes, {"platform", "reference", "notes"})
    kit = LibraryKit(
        code=code.strip().upper(),
        name=name,
        chemistry=chemistry,
        barcode_count=barcode_count,
        **attributes,
    )
    adapter_for(kit.platform).check_kit(chemistry, barcode_count)
    with acting_as(user):
        _save_new(kit)
    return kit


@transaction.atomic
def create_flow_cell_type(*, user, code: str, name: str, **attributes) -> FlowCellType:
    _require_reference_manager(user)
    _reject_unknown(attributes, {"platform", "pore", "notes"})
    cell_type = FlowCellType(code=code.strip().upper(), name=name, **attributes)
    with acting_as(user):
        _save_new(cell_type)
    return cell_type


@transaction.atomic
def create_instrument(*, user, name: str, model: str, **attributes) -> Instrument:
    _require_reference_manager(user)
    _reject_unknown(attributes, {"platform", "serial_number", "notes"})
    instrument = Instrument(name=name, model=model, **attributes)
    with acting_as(user):
        _save_new(instrument)
    return instrument


@transaction.atomic
def register_flow_cell(
    *, user, flow_cell_id: str, type: FlowCellType, pores_at_qc: int | None = None, notes: str = ""
) -> FlowCell:
    if not can_register_flow_cells(user):
        raise PermissionDenied
    if type.is_voided:
        raise ValidationError({"type": _("A voided flow cell type cannot be used.")})
    flow_cell = FlowCell(
        flow_cell_id=adapter_for(type.platform).normalize_flow_cell_id(flow_cell_id),
        type=type,
        pores_at_qc=pores_at_qc,
        notes=notes,
    )
    with acting_as(user):
        _save_new(flow_cell)
    return flow_cell


# --- Libraries --------------------------------------------------------------------------------


@transaction.atomic
def create_library(*, user, project: Project, kit: LibraryKit, **attributes) -> Library:
    _require_editor(user, project)
    _reject_unknown(attributes, LIBRARY_ATTRIBUTES)
    if kit.is_voided:
        raise ValidationError({"kit": _("A voided kit cannot be used.")})
    library = Library(project=project, kit=kit, **attributes)
    if library.prepared_by_id is None:
        library.prepared_by = user
    if library.prepared_on is not None and library.prepared_on > date.today():
        raise ValidationError({"prepared_on": _("A library cannot be prepared in the future.")})
    with acting_as(user):
        _save_new(library)
    return library


def _check_member_sample(library: Library, sample: Sample) -> None:
    if sample.is_voided:
        raise ValidationError({"sample": _("A voided sample cannot be sequenced.")})
    if sample.project_id != library.project_id:
        raise ValidationError(
            {"sample": _("The sample must belong to the project of the library.")}
        )
    if sample.kind not in ("dna_extract", "rna_extract", "amplification_product"):
        raise ValidationError(
            {
                "sample": _(
                    "Only DNA extracts, RNA extracts and amplification products are sequenced."
                )
            }
        )
    if sample.status in UNUSABLE_STATUSES:
        raise ValidationError({"sample": _("This sample is used up or discarded.")})


def _check_barcode(library: Library, barcode) -> int | None:
    kit = library.kit
    if barcode in (None, ""):
        if kit.barcode_count and not library.reconstructed:
            raise ValidationError({"barcode": _("A barcode is required.")})
        return None
    if not kit.barcode_count:
        raise ValidationError({"barcode": _("This kit has no barcodes.")})
    number = adapter_for(kit.platform).parse_barcode(barcode)
    if number > kit.barcode_count:
        raise ValidationError(
            {
                "barcode": _("Barcode %(number)s is outside the kit (1 to %(count)s).")
                % {"number": number, "count": kit.barcode_count}
            }
        )
    if library.members.active().filter(barcode=number).exists():
        raise ValidationError(
            {
                "barcode": _("Barcode %(number)s is already used in this library.")
                % {"number": number}
            }
        )
    return number


@transaction.atomic
def add_member(
    *, user, library: Library, sample: Sample, barcode=None, **attributes
) -> LibraryMember:
    """Put a sample in a draft library under a barcode (a number, ``NB07`` or ``barcode07``)."""
    _require_editor(user, library.project)
    _reject_unknown(attributes, MEMBER_ATTRIBUTES)
    _require_library_state(library, LibraryStatus.DRAFT)
    _check_member_sample(library, sample)
    if library.members.active().filter(sample=sample).exists():
        raise ValidationError({"sample": _("This sample is already in the library.")})
    number = _check_barcode(library, barcode)
    member = LibraryMember(library=library, sample=sample, barcode=number, **attributes)
    with acting_as(user):
        _save_new(member)
    return member


@transaction.atomic
def seal_library(*, user, library: Library) -> Library:
    """Fix the content of the library; from now on it can be sequenced."""
    _require_editor(user, library.project)
    _require_library_state(library, LibraryStatus.DRAFT)
    if not library.members.active().exists():
        raise LibraryStateError(_("Add at least one sample before sealing the library."))
    library.status = LibraryStatus.SEALED
    with acting_as(user):
        library.save()
    return library


# --- Runs -------------------------------------------------------------------------------------


@transaction.atomic
def plan_run(*, user, library: Library, flow_cell: FlowCell, **attributes) -> SequencingRun:
    _require_editor(user, library.project)
    _reject_unknown(attributes, RUN_ATTRIBUTES)
    _require_library_state(library, LibraryStatus.SEALED)
    if flow_cell.is_voided:
        raise ValidationError({"flow_cell": _("A voided flow cell cannot be used.")})
    if flow_cell.runs.active().exists():
        raise ValidationError({"flow_cell": _("This flow cell already has a run.")})
    instrument = attributes.get("instrument")
    if instrument is not None and instrument.is_voided:
        raise ValidationError({"instrument": _("A voided instrument cannot be used.")})
    if instrument is not None and instrument.platform != flow_cell.type.platform:
        raise ValidationError({"instrument": _("This instrument does not run this flow cell.")})
    if library.kit.platform != flow_cell.type.platform:
        raise ValidationError(
            {"flow_cell": _("This flow cell does not suit the kit of the library.")}
        )
    if "run_id" in attributes:
        attributes["run_id"] = adapter_for(flow_cell.type.platform).normalize_run_id(
            attributes["run_id"]
        )
    run = SequencingRun(project=library.project, library=library, flow_cell=flow_cell, **attributes)
    with acting_as(user):
        _save_new(run)
    return run


@transaction.atomic
def start_run(*, user, run: SequencingRun, **attributes) -> SequencingRun:
    """The flow cell is loaded and sequencing started: give the date and, if known, the run ID."""
    _require_editor(user, run.project)
    _reject_unknown(attributes, RUN_ATTRIBUTES - {"reconstructed", "total_reads", "total_bases"})
    _require_run_state(run, RunStatus.PLANNED)
    for name, value in attributes.items():
        setattr(run, name, value)
    if run.run_id:
        run.run_id = adapter_for(run.flow_cell.type.platform).normalize_run_id(run.run_id)
    if run.performed_by_id is None:
        run.performed_by = user
    problem = None
    if run.started_on is None and not run.reconstructed:
        problem = {"started_on": _("Give the start date of the run.")}
    elif run.started_on is not None and run.started_on > date.today():
        problem = {"started_on": _("A run cannot start in the future.")}
    if problem:
        run.refresh_from_db()  # leave the caller's object as it was
        raise ValidationError(problem)
    run.status = RunStatus.RUNNING
    run.full_clean()
    with acting_as(user):
        run.save()
    return run


@transaction.atomic
def abort_run(*, user, run: SequencingRun, reason: str) -> SequencingRun:
    _require_editor(user, run.project)
    _require_run_state(run, RunStatus.PLANNED, RunStatus.RUNNING)
    if not reason.strip():
        raise ValidationError({"reason": _("Give the reason the run was aborted.")})
    run.status = RunStatus.ABORTED
    run.notes = f"{run.notes}\n{_('Aborted')}: {reason.strip()}".strip()
    with acting_as(user):
        run.save()
    return run


@transaction.atomic
def record_result(
    *, user, run: SequencingRun, member: LibraryMember, reads: int, reason: str = "", **attributes
) -> BarcodeResult:
    """Enter, or after completion correct, the reads of one library member."""
    _require_editor(user, run.project)
    _reject_unknown(attributes, RESULT_ATTRIBUTES)
    _require_run_state(run, RunStatus.RUNNING, RunStatus.COMPLETED)
    if member.library_id != run.library_id or member.is_voided:
        raise ValidationError({"member": _("This is not a sample of the library of the run.")})
    existing = run.results.active().filter(member=member).first()
    explained = with_reason(reason) if run.status == RunStatus.COMPLETED else nullcontext()
    with acting_as(user), explained:
        if existing is None:
            result = BarcodeResult(run=run, member=member, reads=reads, **attributes)
            _save_new(result)
        else:
            result = existing
            result.reads = reads
            for name, value in attributes.items():
                setattr(result, name, value)
            result.full_clean()
            result.save()
    return result


@transaction.atomic
def complete_run(*, user, run: SequencingRun, **attributes) -> SequencingRun:
    """Sequencing and basecalling are over. The basecaller and its version must be known."""
    _require_editor(user, run.project)
    _reject_unknown(attributes, BASECALLING_ATTRIBUTES | {"total_reads", "total_bases", "notes"})
    _require_run_state(run, RunStatus.RUNNING)
    for name, value in attributes.items():
        setattr(run, name, value)
    problems = {}
    if not run.reconstructed:
        for name in ("basecaller", "basecaller_version"):
            if not getattr(run, name):
                problems[name] = _("Give the basecaller and its version.")
    in_results = run.results.active().aggregate(total=Sum("reads"))["total"] or 0
    if run.total_reads is not None and in_results > run.total_reads:
        problems["total_reads"] = _("The reads of the samples exceed the reads of the run.")
    if problems:
        run.refresh_from_db()
        raise ValidationError(problems)
    run.status = RunStatus.COMPLETED
    run.full_clean()
    with acting_as(user):
        run.save()
    return run


@transaction.atomic
def reopen_run(*, user, run: SequencingRun, reason: str) -> SequencingRun:
    _require_editor(user, run.project)
    _require_run_state(run, RunStatus.COMPLETED)
    with acting_as(user), with_reason(reason):
        run.status = RunStatus.RUNNING
        run.save()
    return run


# --- Voiding ----------------------------------------------------------------------------------


def _guard_voiding(record) -> None:
    if isinstance(record, Library):
        if record.members.active().exists():
            raise RecordInUseError(_("Void the members of this library first."))
        if record.runs.active().exists():
            raise RecordInUseError(_("Void the runs of this library first."))
    elif isinstance(record, LibraryMember):
        if record.results.active().exists():
            raise RecordInUseError(_("Void the results of this sample first."))
        if record.library.status == LibraryStatus.SEALED and record.library.runs.active().exists():
            raise RecordInUseError(_("The library of this sample is in use by a run."))
    elif isinstance(record, SequencingRun):
        if record.results.active().exists():
            raise RecordInUseError(_("Void the results of this run first."))
    elif isinstance(record, FlowCell):
        if record.runs.active().exists():
            raise RecordInUseError(_("An active run uses this flow cell."))
    elif isinstance(record, LibraryKit):
        if record.libraries.active().exists():
            raise RecordInUseError(_("An active library uses this kit."))
    elif isinstance(record, FlowCellType):
        if record.flow_cells.active().exists():
            raise RecordInUseError(_("An active flow cell has this type."))
    elif isinstance(record, Instrument) and record.runs.active().exists():
        raise RecordInUseError(_("An active run uses this instrument."))


def _project_of(record) -> Project | None:
    if isinstance(record, (Library, SequencingRun)):
        return record.project
    if isinstance(record, LibraryMember):
        return record.library.project
    if isinstance(record, BarcodeResult):
        return record.run.project
    return None  # reference data and flow cells


@transaction.atomic
def void_record(*, user, record, reason: str):
    """Void a library, member, run, result, flow cell, kit, flow cell type or instrument."""
    project = _project_of(record)
    if project is not None:
        _require_editor(user, project)
    elif isinstance(record, FlowCell):
        if not can_register_flow_cells(user):
            raise PermissionDenied
    else:
        _require_reference_manager(user)
    _guard_voiding(record)
    with acting_as(user):
        record.void(user=user, reason=reason)
    return record
