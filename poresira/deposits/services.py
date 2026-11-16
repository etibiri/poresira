# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every change to deposits goes through here: permissions, rules, reasons and audit.

Preparing a deposit builds its study, sample and reads items from a sequencing run, then
``collect.preflight`` says whether it is fit to send. Sending is the work of the agent.
"""

import io
import re
import zipfile
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from poresira.core.models import Project
from poresira.core.permissions import can_edit_project
from poresira.core.reason import acting_as
from poresira.samples.models import SampleKind
from poresira.sequencing.models import SequencingRun
from poresira.storage.models import DataFile, FileKind

from .archive import ArchiveError, EnaArchive, Receipt
from .collect import preflight, reads_spec, root_sample, sample_spec, study_spec
from .ena import reads_manifest, samples_xml, study_xml, submission_xml
from .known import known_for
from .models import (
    AccessionKind,
    Deposit,
    DepositFile,
    DepositItem,
    DepositStatus,
    Environment,
    ItemKind,
    ItemStatus,
    PublicAccession,
)
from .permissions import can_publish

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


PRODUCTION_REUSE = (ItemStatus.ACCEPTED,)
OPTION_NAMES = {"sample_checklist", "library_source", "library_selection", "library_strategy"}


def _accepted_in_production(project: Project, **filters) -> DepositItem | None:
    """An object the archive already accepted for this project in production: its accession is
    kept, so a second deposit does not register the same study or sample twice."""
    return (
        DepositItem.objects.active()
        .filter(
            deposit__project=project,
            deposit__environment=Environment.PRODUCTION,
            deposit__voided_at__isnull=True,
            status__in=PRODUCTION_REUSE,
            **filters,
        )
        .exclude(accession="")
        .order_by("-created_at")
        .first()
    )


def _new_item(deposit: Deposit, reused: tuple[str, str] | None, **fields) -> DepositItem:
    """An item of the deposit. ``reused`` is an accession the archives already hold and where
    it comes from: the item is then accepted from the start and is not sent again."""
    item = DepositItem(deposit=deposit, **fields)
    if reused is not None:
        accession, origin = reused
        item.status, item.accession = ItemStatus.ACCEPTED, accession
        item.message = _("Already registered in %(origin)s.") % {"origin": origin}
    _save_new(item)
    return item


def _reuse_study(deposit: Deposit) -> tuple[str, str] | None:
    earlier = _accepted_in_production(
        deposit.project, kind=ItemKind.STUDY, deposit__title=deposit.title
    )
    return (earlier.accession, earlier.deposit.identifier) if earlier else None


def _reuse_sample(deposit: Deposit, field_sample) -> tuple[str, str] | None:
    """A BioSample that exists already, in the public archives or from an earlier deposit."""
    known = known_for(field_sample).biosample
    if known:
        return known, _("the public archives")
    earlier = _accepted_in_production(deposit.project, kind=ItemKind.SAMPLE, sample=field_sample)
    return (earlier.accession, earlier.deposit.identifier) if earlier else None


def _build_deposit(*, user, run: SequencingRun, deposit: Deposit) -> Deposit:
    """Fill a new draft with the study, the samples and the reads of the library of ``run``."""
    members = list(
        run.library.members.active().select_related("sample__collection_event").order_by("barcode")
    )
    if not members:
        raise ValidationError(_("The library of this run has no sample."))
    production = deposit.environment == Environment.PRODUCTION
    with acting_as(user):
        _save_new(deposit)
        study_reuse = _reuse_study(deposit) if production else None
        _new_item(deposit, study_reuse, kind=ItemKind.STUDY, alias=f"{deposit.identifier}_study")
        biosamples: dict = {}
        for member in members:
            field_sample = root_sample(member.sample)
            if field_sample.pk not in biosamples:
                reuse = _reuse_sample(deposit, field_sample) if production else None
                biosamples[field_sample.pk] = _new_item(
                    deposit,
                    reuse,
                    kind=ItemKind.SAMPLE,
                    alias=f"{deposit.identifier}_{field_sample.identifier}",
                    sample=field_sample,
                )
            reads = _new_item(
                deposit,
                None,
                kind=ItemKind.READS,
                alias=f"{deposit.identifier}_{member.sample.identifier}_{run.identifier}",
                sample=member.sample,
                sequencing_run=run,
                sample_item=biosamples[field_sample.pk],
            )
            for file in reads_files_of(run, member.barcode, single_sample=len(members) == 1):
                _save_new(DepositFile(item=reads, file=file, sha256=file.sha256))
    return deposit


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

    Members without a reads file are kept, so that the pre-flight report names them. A deposit
    is prepared for the test service; ``copy_for_production`` makes the real one."""
    _require_editor(user, run.project)
    if run.is_voided:
        raise ValidationError(_("A voided run cannot be deposited."))
    if environment == Environment.PRODUCTION:
        raise ValidationError(
            _("Prepare the deposit for the test service; production is a separate step.")
        )
    if unknown := set(options) - OPTION_NAMES:
        raise TypeError(f"Unexpected options: {sorted(unknown)}")
    deposit = Deposit(
        project=run.project,
        title=title.strip(),
        description=description,
        environment=environment,
        hold_until=hold_until,
        **options,
    )
    return _build_deposit(user=user, run=run, deposit=deposit)


@transaction.atomic
def copy_for_production(
    *, user, source: Deposit, hold_until: date, permits_confirmed: bool
) -> Deposit:
    """Prepare the real deposit of what a test deposit proved to work.

    It needs a test deposit the archive accepted, a release date in the future, the confirmation
    that sampling and benefit-sharing permits were checked, and a person allowed to publish.
    Studies and samples the archive already accepted in production keep their accessions."""
    project = source.project
    if not can_publish(user, project):
        raise PermissionDenied
    if source.environment != Environment.TEST or source.status != DepositStatus.ACCEPTED:
        raise DepositServiceError(_("Only a test deposit that the archive accepted can be copied."))
    if source.is_voided:
        raise DepositServiceError(_("A voided deposit cannot be copied."))
    if not permits_confirmed:
        raise ValidationError(
            {"compliance_confirmed_at": _("Confirm that the permits were checked.")}
        )
    if hold_until <= date.today():
        raise ValidationError({"hold_until": _("Give a release date in the future.")})
    run = source.items.active().filter(kind=ItemKind.READS).first().sequencing_run
    deposit = Deposit(
        project=project,
        title=source.title,
        description=source.description,
        environment=Environment.PRODUCTION,
        hold_until=hold_until,
        compliance_confirmed_at=timezone.now(),
        compliance_confirmed_by=user,
        sample_checklist=source.sample_checklist,
        library_source=source.library_source,
        library_selection=source.library_selection,
        library_strategy=source.library_strategy,
    )
    return _build_deposit(user=user, run=run, deposit=deposit)


# --- Sending ----------------------------------------------------------------------------------

MAX_RECEIPT_CHARS = 50_000


def note_problem(deposit: Deposit, message: str) -> None:
    """Keep a problem in the notes of the deposit, outside any transaction being undone."""
    with transaction.atomic():
        current = Deposit.objects.select_for_update().get(pk=deposit.pk)
        current.notes = f"{current.notes}\n{message}".strip()
        current.save(update_fields=["notes", "updated_at"])


def ready_to_send(user, deposit: Deposit, confirm: str = "") -> None:
    """Raise unless this person may send this deposit now (the web page asks before it queues)."""
    _require_editor(user, deposit.project)
    if deposit.is_voided or deposit.status != DepositStatus.DRAFT:
        raise DepositServiceError(_("Only a draft deposit can be submitted."))
    if deposit.environment == Environment.PRODUCTION:
        if not can_publish(user, deposit.project):
            raise PermissionDenied
        if confirm.strip() != deposit.project.code:
            raise DepositServiceError(
                _("Type the code of the project (%(code)s) to confirm a public deposit.")
                % {"code": deposit.project.code}
            )
    report = preflight(deposit)
    if not report.ready:
        raise DepositServiceError(
            _("The deposit is not ready: %(problems)s")
            % {"problems": " ".join(finding.message for finding in report.errors)}
        )


@transaction.atomic
def _begin(user, deposit: Deposit, webin_user: str) -> Deposit:
    current = Deposit.objects.select_for_update().get(pk=deposit.pk)
    if current.status != DepositStatus.DRAFT:
        raise DepositServiceError(_("Only a draft deposit can be submitted."))
    current.status = DepositStatus.SUBMITTING
    current.submitted_by, current.submitted_at = user, timezone.now()
    current.webin_user = webin_user
    with acting_as(user):
        current.save()
    return current


@transaction.atomic
def _record_item(
    user, item: DepositItem, *, accepted: bool, accession="", secondary="", message=""
):
    current = DepositItem.objects.select_for_update().get(pk=item.pk)
    current.status = ItemStatus.ACCEPTED if accepted else ItemStatus.REJECTED
    current.accession, current.secondary_accession = accession, secondary
    current.message = message[:2000]
    with acting_as(user):
        current.save()
    return current


@transaction.atomic
def _finish(user, deposit: Deposit, receipts: list[str]) -> Deposit:
    current = Deposit.objects.select_for_update().get(pk=deposit.pk)
    items = current.items.active()
    accepted = not items.exclude(status=ItemStatus.ACCEPTED).exists()
    current.status = DepositStatus.ACCEPTED if accepted else DepositStatus.REJECTED
    current.finished_at = timezone.now()
    current.receipt = "\n".join(receipts)[:MAX_RECEIPT_CHARS]
    with acting_as(user):
        current.save()
    return current


def _problems(receipt: Receipt, archive) -> str:
    return archive.scrub(" ".join(receipt.errors) or "The archive did not accept it.")


def _register_study_and_samples(user, deposit, archive, receipts: list[str]) -> None:
    items = list(deposit.items.active().select_related("sample__collection_event"))
    study = next(item for item in items if item.kind == ItemKind.STUDY)
    samples = [i for i in items if i.kind == ItemKind.SAMPLE and i.status != ItemStatus.ACCEPTED]
    study_pending = study.status != ItemStatus.ACCEPTED
    if not study_pending and not samples:
        return
    try:
        receipt = archive.register(
            study_xml(study_spec(deposit)) if study_pending else None,
            samples_xml([sample_spec(item) for item in samples], deposit.sample_checklist)
            if samples
            else None,
            submission_xml(deposit.hold_until),
        )
    except ArchiveError as error:
        message = archive.scrub(str(error))
        for item in ([study] if study_pending else []) + samples:
            _record_item(user, item, accepted=False, message=message)
        return
    receipts.append(archive.scrub(receipt.text))
    pending = ([(study, "PROJECT", "PRJ")] if study_pending else []) + [
        (item, "SAMPLE", "SAM") for item in samples
    ]
    for item, tag, prefix in pending:
        accession = receipt.accession_of(tag, item.alias, prefix) if receipt.success else ""
        if accession:
            _record_item(user, item, accepted=True, accession=accession)
        else:
            message = _problems(receipt, archive) if not receipt.success else _("No accession.")
            _record_item(user, item, accepted=False, message=message)


def _submit_reads(user, deposit, archive, receipts: list[str]) -> None:
    items = list(deposit.items.active().select_related("sample_item", "sequencing_run__library"))
    study = next(item for item in items if item.kind == ItemKind.STUDY)
    for item in (i for i in items if i.kind == ItemKind.READS and i.status != ItemStatus.ACCEPTED):
        if not study.accession or not item.sample_item.accession:
            message = _("The study or the sample of these reads was not registered.")
            _record_item(user, item, accepted=False, message=message)
            continue
        manifest = reads_manifest(
            reads_spec(item, study=study.accession, sample=item.sample_item.accession)
        )
        try:
            receipt = archive.submit_reads(manifest, item.alias, deposit.identifier)
        except ArchiveError as error:
            _record_item(user, item, accepted=False, message=archive.scrub(str(error)))
            continue
        receipts.append(archive.scrub(receipt.text))
        run_accession, experiment = receipt.first("RUN", "ERR"), receipt.first("EXPERIMENT", "ERX")
        if receipt.success and run_accession:
            _record_item(user, item, accepted=True, accession=run_accession, secondary=experiment)
        else:
            _record_item(user, item, accepted=False, message=_problems(receipt, archive))


def submit_deposit(*, user, deposit: Deposit, confirm: str = "", archive_factory=EnaArchive):
    """Send a draft deposit to the archive: the study and samples, then the reads of each sample.

    The deposit is checked first and marked as being submitted before anything leaves the
    computer. Each object is recorded as the archive answers, so an interruption leaves what
    was accepted in place. Production needs a person allowed to publish and the project code
    typed as confirmation. Run it on the agent: it reaches the archive."""
    ready_to_send(user, deposit, confirm)
    archive = archive_factory(deposit.environment)  # refuses when no Webin account is set
    deposit = _begin(user, deposit, archive.user)
    receipts: list[str] = []
    try:
        _register_study_and_samples(user, deposit, archive, receipts)
        _submit_reads(user, deposit, archive, receipts)
    finally:
        finished = _finish(user, deposit, receipts)
    return finished


@transaction.atomic
def void_record(*, user, deposit: Deposit, reason: str) -> Deposit:
    """Void a deposit that is a draft or finished; one being submitted must finish first."""
    _require_editor(user, deposit.project)
    if deposit.status == DepositStatus.SUBMITTING:
        raise DepositServiceError(_("A deposit being submitted cannot be voided."))
    with acting_as(user):
        deposit.void(user=user, reason=reason)
    return deposit


# --- Accessions that already exist -----------------------------------------------------------


@transaction.atomic
def record_public_accession(
    *, user, project: Project, kind: str, accession: str, sample=None, released_on=None, notes=""
) -> PublicAccession:
    """Record an accession a public archive already gave: a BioProject (no sample), or for a
    field sample its BioSample or its SRA experiment or run."""
    _require_editor(user, project)
    if kind not in AccessionKind.values:
        raise ValidationError({"kind": _("Unknown kind of accession.")})
    if sample is not None:
        if sample.is_voided or sample.project_id != project.pk:
            raise ValidationError({"sample": _("The sample must belong to the project.")})
        if sample.kind not in (SampleKind.FIELD_SAMPLE, SampleKind.LIVING_PLANT):
            raise ValidationError(
                {"sample": _("Give the field sample or living plant, not an extract or product.")}
            )
    record = PublicAccession(
        project=project,
        kind=kind,
        accession=accession.strip().upper(),
        sample=sample,
        released_on=released_on,
        notes=notes,
    )
    with acting_as(user):
        _save_new(record)
    return record


@transaction.atomic
def void_item(*, user, item: DepositItem, reason: str) -> DepositItem:
    """Leave an object out of a draft deposit, for example reads the archives already hold."""
    deposit = item.deposit
    _require_editor(user, deposit.project)
    if deposit.status != DepositStatus.DRAFT or deposit.is_voided:
        raise DepositServiceError(_("Items can be left out of a draft deposit only."))
    if item.kind == ItemKind.STUDY:
        raise DepositServiceError(_("The study cannot be left out."))
    if item.kind == ItemKind.SAMPLE and item.reads_items.active().exists():
        raise DepositServiceError(_("Leave out the reads of this sample first."))
    with acting_as(user):
        item.void(user=user, reason=reason)
    return item


def submission_files(deposit: Deposit) -> dict[str, str]:
    """The text that would be sent, for the person to read first. Accessions the archive has not
    given yet appear as placeholders."""
    items = list(deposit.items.active().select_related("sample__collection_event", "sample_item"))
    study = next((i for i in items if i.kind == ItemKind.STUDY), None)
    samples = [i for i in items if i.kind == ItemKind.SAMPLE and i.status != ItemStatus.ACCEPTED]
    files = {"submission.xml": submission_xml(deposit.hold_until)}
    if study is not None and study.status != ItemStatus.ACCEPTED:
        files["study.xml"] = study_xml(study_spec(deposit))
    if samples:
        files["samples.xml"] = samples_xml(
            [sample_spec(item) for item in samples], deposit.sample_checklist
        )
    for item in (i for i in items if i.kind == ItemKind.READS):
        manifest = reads_manifest(
            reads_spec(
                item,
                study=(study.accession if study else "") or "<STUDY ACCESSION>",
                sample=item.sample_item.accession or "<SAMPLE ACCESSION>",
            )
        )
        files[f"manifests/{item.alias}.txt"] = manifest
    return files


def submission_zip(deposit: Deposit) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in submission_files(deposit).items():
            archive.writestr(name, text)
    return buffer.getvalue()
