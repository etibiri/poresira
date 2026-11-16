# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every change to deposits goes through here: permissions, rules, reasons and audit.

Preparing a deposit builds its study, sample and reads items from a sequencing run, then
``collect.preflight`` says whether it is fit to send. Sending is the work of the agent.
"""

import re
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from poresira.core.models import Project
from poresira.core.permissions import can_edit_project
from poresira.core.reason import acting_as
from poresira.sequencing.models import SequencingRun
from poresira.storage.models import DataFile, FileKind

from .collect import root_sample
from .models import Deposit, DepositFile, DepositItem, DepositStatus, Environment, ItemKind

BARCODE_IN_PATH = re.compile(r"(?i)(?<![a-z0-9])(?:barcode|bc|nb)0*(\d+)(?!\d)")
READS_KINDS = (FileKind.FASTQ, FileKind.BAM)


class DepositServiceError(Exception):
    """A rule of the deposits module was not respected."""


def _require_editor(user, project: Project) -> None:
    if not can_edit_project(user, project):
        raise PermissionDenied


def _save_new(instance) -> None:
    instance.full_clean(exclude=["identifier"])
    instance.save()


def _barcode_of(path: str) -> int | None:
    """The barcode number a reads file path names (``barcode07``, ``BC07``, ``NB07``)."""
    for match in BARCODE_IN_PATH.finditer(path):
        return int(match.group(1))
    return None


def reads_files_of(run: SequencingRun, barcode: int | None, single_sample: bool) -> list[DataFile]:
    """The FASTQ files of a run that belong to one barcode, or BAM files when there is no FASTQ.

    A run that was not barcoded has all its reads files in its only sample."""
    candidates = [
        file
        for file in run.files.active().filter(kind__in=READS_KINDS).select_related("backend")
        if (barcode is not None and _barcode_of(file.path) == barcode)
        or (barcode is None and single_sample)
    ]
    for kind in READS_KINDS:
        of_kind = [file for file in candidates if file.kind == kind]
        if of_kind:
            return sorted(of_kind, key=lambda file: file.path)
    return []


@transaction.atomic
def prepare_deposit(
    *,
    user,
    run: SequencingRun,
    title: str,
    description: str = "",
    environment: str = Environment.TEST,
    hold_until: date | None = None,
    **options,
) -> Deposit:
    """Create a draft deposit for the samples of the library of ``run``: the study, one sample
    per field sample, and the reads of each library member with the files that carry its barcode.

    Members without a reads file are kept, so that the pre-flight report names them."""
    project = run.project
    _require_editor(user, project)
    if run.is_voided:
        raise ValidationError(_("A voided run cannot be deposited."))
    if environment == Environment.PRODUCTION:
        raise ValidationError(
            _("Prepare the deposit for the test service; production is a separate step.")
        )
    allowed = {"sample_checklist", "library_source", "library_selection", "library_strategy"}
    if unknown := set(options) - allowed:
        raise TypeError(f"Unexpected options: {sorted(unknown)}")
    members = list(
        run.library.members.active().select_related("sample__collection_event").order_by("barcode")
    )
    if not members:
        raise ValidationError(_("The library of this run has no sample."))
    deposit = Deposit(
        project=project,
        title=title.strip(),
        description=description,
        environment=environment,
        hold_until=hold_until,
        **options,
    )
    with acting_as(user):
        _save_new(deposit)
        _save_new(
            DepositItem(deposit=deposit, kind=ItemKind.STUDY, alias=f"{deposit.identifier}_study")
        )
        biosamples: dict = {}
        for member in members:
            field_sample = root_sample(member.sample)
            if field_sample.pk not in biosamples:
                biosamples[field_sample.pk] = DepositItem(
                    deposit=deposit,
                    kind=ItemKind.SAMPLE,
                    alias=f"{deposit.identifier}_{field_sample.identifier}",
                    sample=field_sample,
                )
                _save_new(biosamples[field_sample.pk])
            reads = DepositItem(
                deposit=deposit,
                kind=ItemKind.READS,
                alias=f"{deposit.identifier}_{member.sample.identifier}_{run.identifier}",
                sample=member.sample,
                sequencing_run=run,
                sample_item=biosamples[field_sample.pk],
            )
            _save_new(reads)
            for file in reads_files_of(run, member.barcode, single_sample=len(members) == 1):
                _save_new(DepositFile(item=reads, file=file, sha256=file.sha256))
    return deposit


@transaction.atomic
def void_record(*, user, deposit: Deposit, reason: str) -> Deposit:
    """Void a deposit that is a draft or finished; one being submitted must finish first."""
    _require_editor(user, deposit.project)
    if deposit.status == DepositStatus.SUBMITTING:
        raise DepositServiceError(_("A deposit being submitted cannot be voided."))
    with acting_as(user):
        deposit.void(user=user, reason=reason)
    return deposit
