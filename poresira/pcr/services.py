# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every change to PCR records goes through here: permissions, rules, reasons and audit context.

Functions run in one transaction. Once a run is closed, a result changes only with a reason;
the database enforces it too.
"""

import re
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from poresira.core.models import Project
from poresira.core.permissions import can_edit_project
from poresira.core.reason import acting_as, with_reason
from poresira.samples.models import Sample, SampleKind, SampleStatus

from .models import (
    PLATE_COLUMNS,
    PLATE_ROWS,
    Assay,
    Container,
    CyclingProgram,
    CyclingStep,
    MasterMix,
    MasterMixComponent,
    PcrReaction,
    PcrRun,
    Primer,
    PrimerDirection,
    Result,
    Role,
    RunStatus,
    StepStage,
)
from .permissions import can_manage_reference_data

PLATE_POSITION = re.compile(rf"^([A-{chr(64 + PLATE_ROWS)}])0*(\d{{1,2}})$")
TUBE_POSITION = re.compile(r"^0*(\d{1,3})$")
STAGE_ORDER = [StepStage.INITIAL, StepStage.CYCLE, StepStage.FINAL]
RUN_ATTRIBUTES = {
    "performed_on",
    "performed_by",
    "thermocycler",
    "master_mix_lot",
    "polymerase_lot",
    "reconstructed",
    "notes",
}
REACTION_ATTRIBUTES = {"template_volume_ul", "notes"}
RESULT_ATTRIBUTES = {"ct", "band_size_bp", "notes"}
UNUSABLE_TEMPLATE_STATUSES = {SampleStatus.CONSUMED, SampleStatus.DISCARDED}


class PcrServiceError(Exception):
    """A rule of the PCR module was not respected."""


class InvalidPositionError(PcrServiceError):
    pass


class PositionTakenError(PcrServiceError):
    pass


class RunStateError(PcrServiceError):
    """The run is not in a state that allows this change."""


class RecordInUseError(PcrServiceError):
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


# --- Reference data ---------------------------------------------------------------------------


@transaction.atomic
def create_primer(*, user, name: str, sequence: str, direction: str, **attributes) -> Primer:
    _require_reference_manager(user)
    _reject_unknown(attributes, {"target", "reference", "notes"})
    primer = Primer(name=name, sequence=sequence.strip().upper(), direction=direction, **attributes)
    with acting_as(user):
        _save_new(primer)
    return primer


def _check_program_steps(steps: list[dict]) -> None:
    stages = [step["stage"] for step in steps]
    ranks = [STAGE_ORDER.index(stage) for stage in stages]
    if ranks != sorted(ranks):
        raise ValidationError(
            _("Steps must go from the initial steps to the cycle to the final steps.")
        )
    if StepStage.CYCLE not in stages:
        raise ValidationError(_("A program needs at least one step inside the cycle."))


@transaction.atomic
def create_cycling_program(
    *, user, name: str, cycles: int, steps: list[dict], **attributes
) -> CyclingProgram:
    """``steps`` lists dicts with ``stage``, ``temperature_c``, ``duration_seconds``, ``label``."""
    _require_reference_manager(user)
    _reject_unknown(attributes, {"reference", "notes"})
    _check_program_steps(steps)
    program = CyclingProgram(name=name, cycles=cycles, **attributes)
    with acting_as(user):
        _save_new(program)
        for order, step in enumerate(steps, start=1):
            _save_new(CyclingStep(program=program, order=order, **step))
    return program


@transaction.atomic
def create_master_mix(
    *, user, name: str, reaction_volume_ul, components: list[dict], **attributes
) -> MasterMix:
    """``components`` lists dicts with ``name``, ``volume_ul`` and optionally ``concentration``."""
    _require_reference_manager(user)
    _reject_unknown(attributes, {"polymerase", "reference", "notes"})
    total = sum((Decimal(str(part["volume_ul"])) for part in components), Decimal(0))
    if total > Decimal(str(reaction_volume_ul)):
        raise ValidationError(_("The ingredients add up to more than the reaction volume."))
    mix = MasterMix(name=name, reaction_volume_ul=reaction_volume_ul, **attributes)
    with acting_as(user):
        _save_new(mix)
        for part in components:
            _save_new(MasterMixComponent(master_mix=mix, **part))
    return mix


@transaction.atomic
def create_assay(
    *,
    user,
    name: str,
    target: str,
    forward_primer: Primer,
    reverse_primer: Primer,
    cycling_program: CyclingProgram,
    **attributes,
) -> Assay:
    _require_reference_manager(user)
    _reject_unknown(
        attributes,
        {"master_mix", "reverse_transcription", "expected_amplicon_bp", "reference", "notes"},
    )
    if forward_primer.direction != PrimerDirection.FORWARD:
        raise ValidationError({"forward_primer": _("This primer is not a forward primer.")})
    if reverse_primer.direction != PrimerDirection.REVERSE:
        raise ValidationError({"reverse_primer": _("This primer is not a reverse primer.")})
    used = [forward_primer, reverse_primer, cycling_program, attributes.get("master_mix")]
    if any(record is not None and record.is_voided for record in used):
        raise ValidationError(_("A voided primer, program or master mix cannot be used."))
    assay = Assay(
        name=name,
        target=target,
        forward_primer=forward_primer,
        reverse_primer=reverse_primer,
        cycling_program=cycling_program,
        **attributes,
    )
    with acting_as(user):
        _save_new(assay)
    return assay


# --- Layout -----------------------------------------------------------------------------------


def normalize_position(run: PcrRun, position: str) -> str:
    """Return ``A1`` for ``a01`` in a plate, ``3`` for ``03`` in tubes; refuse anything outside."""
    position = position.strip().upper()
    if run.container == Container.PLATE_96:
        match = PLATE_POSITION.match(position)
        if match and 1 <= int(match.group(2)) <= PLATE_COLUMNS:
            return f"{match.group(1)}{int(match.group(2))}"
        raise InvalidPositionError(
            _("%(position)s is not a well of a 96-well plate (A1 to H12).") % {"position": position}
        )
    match = TUBE_POSITION.match(position)
    if match and 1 <= int(match.group(1)) <= run.tube_count:
        return str(int(match.group(1)))
    raise InvalidPositionError(
        _("%(position)s is not a tube of this run (1 to %(count)s).")
        % {"position": position, "count": run.tube_count}
    )


def all_positions(run: PcrRun) -> list[str]:
    """Every position of the run, filled column by column in a plate (A1, B1, ... H1, A2, ...)."""
    if run.container == Container.TUBES:
        return [str(number) for number in range(1, run.tube_count + 1)]
    rows = [chr(ord("A") + row) for row in range(PLATE_ROWS)]
    return [f"{row}{column}" for column in range(1, PLATE_COLUMNS + 1) for row in rows]


def next_free_position(run: PcrRun) -> str:
    taken = set(run.reactions.active().values_list("position", flat=True))
    for position in all_positions(run):
        if position not in taken:
            return position
    raise PositionTakenError(_("Every position of this run is already used."))


# --- Runs -------------------------------------------------------------------------------------


@transaction.atomic
def plan_run(
    *,
    user,
    project: Project,
    assay: Assay,
    container: str = Container.PLATE_96,
    tube_count: int | None = None,
    **attributes,
) -> PcrRun:
    _require_editor(user, project)
    _reject_unknown(attributes, RUN_ATTRIBUTES)
    if assay.is_voided:
        raise ValidationError({"assay": _("A voided assay cannot be used.")})
    run = PcrRun(
        project=project, assay=assay, container=container, tube_count=tube_count, **attributes
    )
    with acting_as(user):
        _save_new(run)
    return run


def _require_state(run: PcrRun, *allowed: str) -> None:
    if run.is_voided:
        raise RunStateError(_("A voided run cannot be changed."))
    if run.status not in allowed:
        raise RunStateError(
            _("This run is %(status)s: the change is not possible.")
            % {"status": RunStatus(run.status).label.lower()}
        )


def _check_template(run: PcrRun, sample: Sample) -> None:
    if sample.is_voided:
        raise ValidationError({"sample": _("A voided sample cannot be a template.")})
    if sample.project_id != run.project_id:
        raise ValidationError({"sample": _("The template must belong to the project of the run.")})
    if sample.kind == SampleKind.LIVING_PLANT:
        raise ValidationError(
            {"sample": _("A living plant cannot be a template; use an extract of it.")}
        )
    if sample.status in UNUSABLE_TEMPLATE_STATUSES:
        raise ValidationError({"sample": _("This sample is used up or discarded.")})


def _check_template_volume(run: PcrRun, volume) -> None:
    mix = run.assay.master_mix
    if volume is None or mix is None:
        return
    room = mix.reaction_volume_ul - sum(
        (part.volume_ul for part in mix.components.all()), Decimal(0)
    )
    if Decimal(str(volume)) > room:
        raise ValidationError(
            {
                "template_volume_ul": _("The master mix leaves room for %(room)s µL of template.")
                % {"room": room.normalize()}
            }
        )


@transaction.atomic
def add_reaction(
    *,
    user,
    run: PcrRun,
    position: str = "",
    role: str = Role.SAMPLE,
    sample: Sample | None = None,
    **attributes,
) -> PcrReaction:
    """Put a template or a control in a well or tube; no ``position`` takes the next free one."""
    _require_editor(user, run.project)
    _reject_unknown(attributes, REACTION_ATTRIBUTES)
    _require_state(run, RunStatus.PLANNED)
    if sample is not None:
        _check_template(run, sample)
    _check_template_volume(run, attributes.get("template_volume_ul"))
    position = normalize_position(run, position) if position.strip() else next_free_position(run)
    if run.reactions.active().filter(position=position).exists():
        raise PositionTakenError(
            _("Position %(position)s of this run is already used.") % {"position": position}
        )
    reaction = PcrReaction(run=run, position=position, role=role, sample=sample, **attributes)
    with acting_as(user):
        _save_new(reaction)
    return reaction


@transaction.atomic
def mark_performed(*, user, run: PcrRun, **attributes) -> PcrRun:
    """The run went through the thermocycler: the layout is now fixed and results may be entered."""
    _require_editor(user, run.project)
    _reject_unknown(attributes, RUN_ATTRIBUTES)
    _require_state(run, RunStatus.PLANNED)
    if not run.reactions.active().exists():
        raise RunStateError(_("Add at least one reaction before marking the run as performed."))
    for name, value in attributes.items():
        setattr(run, name, value)
    if run.performed_by_id is None:
        run.performed_by = user
    problems = {}
    if run.performed_on is None and not run.reconstructed:
        problems["performed_on"] = _("Give the date of the run.")
    elif run.performed_on is not None and run.performed_on > date.today():
        problems["performed_on"] = _("A run cannot take place in the future.")
    if problems:
        run.refresh_from_db()  # leave the caller's object as it was
        raise ValidationError(problems)
    run.status = RunStatus.PERFORMED
    run.full_clean()
    with acting_as(user):
        run.save()
    return run


@transaction.atomic
def record_result(
    *, user, reaction: PcrReaction, result: str, reason: str = "", **attributes
) -> PcrReaction:
    """Enter or correct a result. After the run is closed a ``reason`` is required."""
    run = reaction.run
    _require_editor(user, run.project)
    _reject_unknown(attributes, RESULT_ATTRIBUTES)
    _require_state(run, RunStatus.PERFORMED, RunStatus.CLOSED)
    if reaction.is_voided:
        raise RunStateError(_("A voided reaction cannot be changed."))
    if result == Result.PENDING:
        raise ValidationError({"result": _("A recorded result cannot go back to pending.")})
    explained = with_reason(reason) if run.status == RunStatus.CLOSED else nullcontext()
    with acting_as(user), explained:  # a blank reason is refused before anything is changed
        reaction.result = result
        for name, value in attributes.items():
            setattr(reaction, name, value)
        reaction.full_clean()
        reaction.save()
    return reaction


@transaction.atomic
def close_run(*, user, run: PcrRun) -> PcrRun:
    _require_editor(user, run.project)
    _require_state(run, RunStatus.PERFORMED)
    if run.reactions.active().filter(result=Result.PENDING).exists():
        raise RunStateError(_("Every reaction needs a result before the run is closed."))
    run.status = RunStatus.CLOSED
    with acting_as(user):
        run.save()
    return run


@transaction.atomic
def reopen_run(*, user, run: PcrRun, reason: str) -> PcrRun:
    _require_editor(user, run.project)
    _require_state(run, RunStatus.CLOSED)
    with acting_as(user), with_reason(reason):
        run.status = RunStatus.PERFORMED
        run.save()
    return run


# --- Validity of a run ------------------------------------------------------------------------


class Validity:
    VALID = "valid"
    INVALID = "invalid"
    NOT_ASSESSED = "not_assessed"


@dataclass(frozen=True)
class RunValidity:
    """Whether the controls let the results of a run be trusted."""

    state: str
    problems: tuple[str, ...] = ()


def assess_run(run: PcrRun) -> RunValidity:
    """Valid only if every positive control is positive and every blank or no-template control is
    negative. Without controls, or with a control still pending, nothing is concluded."""
    if run.reconstructed:
        return RunValidity(
            Validity.NOT_ASSESSED, (_("Reconstructed from a publication: controls are unknown."),)
        )
    controls = [r for r in run.reactions.active() if r.role != Role.SAMPLE]
    positives = [r for r in controls if r.role == Role.POSITIVE_CONTROL]
    negatives = [r for r in controls if r.role != Role.POSITIVE_CONTROL]
    problems = []
    for control in positives:
        if control.result in (Result.NEGATIVE, Result.INCONCLUSIVE):
            problems.append(
                _("Positive control %(position)s did not amplify.") % {"position": control.position}
            )
    for control in negatives:
        if control.result in (Result.POSITIVE, Result.INCONCLUSIVE):
            problems.append(
                _("Control %(position)s is contaminated.") % {"position": control.position}
            )
    if problems:
        return RunValidity(Validity.INVALID, tuple(problems))
    missing = []
    if not positives:
        missing.append(_("No positive control."))
    if not any(r.role == Role.NEGATIVE_CONTROL for r in negatives):
        missing.append(_("No no-template control."))
    if any(r.result == Result.PENDING for r in controls):
        missing.append(_("A control has no result yet."))
    if missing:
        return RunValidity(Validity.NOT_ASSESSED, tuple(missing))
    return RunValidity(Validity.VALID)


def band_matches_expectation(reaction: PcrReaction, tolerance: float = 0.1) -> bool | None:
    """Whether the band on the gel is within ``tolerance`` of the assay's expected size."""
    expected = reaction.run.assay.expected_amplicon_bp
    if not expected or not reaction.band_size_bp:
        return None
    return abs(reaction.band_size_bp - expected) <= expected * tolerance


# --- Voiding ----------------------------------------------------------------------------------


def _guard_voiding(record) -> None:
    if isinstance(record, PcrRun) and record.reactions.active().exists():
        raise RecordInUseError(_("Void the reactions of this run first."))
    if (
        isinstance(record, Primer)
        and Assay.objects.active()
        .filter(Q(forward_primer=record) | Q(reverse_primer=record))
        .exists()
    ):
        raise RecordInUseError(_("An active assay still uses this primer."))
    if isinstance(record, (CyclingProgram, MasterMix)) and record.assays.active().exists():
        raise RecordInUseError(_("An active assay still uses this record."))
    if isinstance(record, Assay) and record.runs.active().exists():
        raise RecordInUseError(_("Void the runs of this assay first."))


@transaction.atomic
def void_record(*, user, record, reason: str):
    """Void a run, a reaction, a primer, a program, a master mix or an assay."""
    if isinstance(record, PcrReaction):
        _require_editor(user, record.run.project)
    elif isinstance(record, PcrRun):
        _require_editor(user, record.project)
    else:
        _require_reference_manager(user)
    _guard_voiding(record)
    with acting_as(user):
        record.void(user=user, reason=reason)
    return record
