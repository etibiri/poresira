# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn the records of a deposit into what the archive is sent, and check they are fit to send."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.utils.translation import gettext as _

from poresira.samples.models import Sample, SampleKind
from poresira.sequencing.models import RunStatus
from poresira.storage.models import Integrity

from . import ncbi
from .ena import (
    Attribute,
    ReadFile,
    ReadsSpec,
    SampleSpec,
    StudySpec,
    ena_instrument,
)
from .known import known_bioproject, known_for
from .models import Archive, Deposit, DepositItem, Environment, ItemKind, ItemStatus

DAMAGED = (Integrity.MISMATCH, Integrity.MISSING, Integrity.UNREADABLE)
COORDINATE_DECIMALS_PUBLIC = 2  # 0.01 degree is about a kilometre


@dataclass(frozen=True)
class Finding:
    """A problem found before sending. An error stops the submission; a warning does not."""

    message: str
    item: DepositItem | None = None


@dataclass
class Report:
    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.errors


BIOLOGICAL_KINDS = (SampleKind.FIELD_SAMPLE, SampleKind.LIVING_PLANT)


def biological_sample(sample: Sample) -> Sample:
    """The material the archive's BioSample describes: the nearest field sample or living plant,
    going up from the extract or product that was sequenced (a greenhouse plant keeps its own
    BioSample; a field sample's extracts share the field sample's)."""
    current = sample
    while current.kind not in BIOLOGICAL_KINDS and current.parent_id is not None:
        current = current.parent
    return current


root_sample = biological_sample  # the name used before greenhouse plants had their own BioSample


def study_spec(deposit: Deposit) -> StudySpec:
    study = deposit.items.active().filter(kind=ItemKind.STUDY).first()
    return StudySpec(
        alias=study.alias if study else f"{deposit.identifier}_study",
        title=deposit.title,
        description=deposit.description,
    )


def sample_spec(item: DepositItem) -> SampleSpec:
    """The BioSample of a field sample, without the names of people (the collector stays out)."""
    sample = item.sample
    event = sample.collection_event
    attributes = []
    if event is not None:
        if event.collected_on:
            attributes.append(Attribute("collection date", event.collected_on.isoformat()))
        if event.country:
            attributes.append(Attribute("geographic location (country and/or sea)", event.country))
        place = ", ".join(part for part in (event.region, event.province, event.locality) if part)
        if place:
            attributes.append(Attribute("geographic location (region and locality)", place))
        if event.latitude is not None and event.longitude is not None:
            attributes += [
                Attribute("geographic location (latitude)", _plain(event.latitude), "DD"),
                Attribute("geographic location (longitude)", _plain(event.longitude), "DD"),
            ]
    if sample.host_taxon:
        attributes.append(Attribute("host scientific name", sample.host_taxon))
    if sample.host_variety:
        attributes.append(Attribute("host variety", sample.host_variety))
    if sample.symptom_score is not None:
        attributes.append(Attribute("symptom severity (scale 1 to 9)", str(sample.symptom_score)))
    return SampleSpec(
        alias=item.alias,
        title=sample.label or sample.identifier,
        scientific_name=sample.host_taxon,
        tax_id=sample.host_tax_id or 0,
        attributes=tuple(attributes),
    )


def reads_spec(item: DepositItem, *, study: str, sample: str) -> ReadsSpec:
    """The manifest content of a reads item; ``study`` and ``sample`` are archive accessions."""
    deposit, run = item.deposit, item.sequencing_run
    instrument = ena_instrument(run.instrument.model) if run.instrument else None
    files = tuple(
        ReadFile(kind=row.file.kind, path=str(Path(row.file.backend.root) / row.file.path))
        for row in item.files.active().select_related("file__backend").order_by("file__path")
    )
    return ReadsSpec(
        name=item.alias,
        study=study,
        sample=sample,
        instrument=instrument or "unspecified",
        library_source=deposit.library_source,
        library_selection=deposit.library_selection,
        library_strategy=deposit.library_strategy,
        files=files,
        library_name=run.library.name or run.library.identifier,
        description=f"{run.library.kit.code} on {run.flow_cell.type.code}",
    )


def _plain(value: Decimal) -> str:
    """The number without the zeros the database pads it with: 11.08000 is 11.08."""
    return format(value.normalize(), "f")


def _decimals(value: Decimal) -> int:
    return max(0, -value.normalize().as_tuple().exponent)


def preflight(deposit: Deposit, today: date | None = None) -> Report:
    """Everything that would stop, or deserve a second look before, sending this deposit."""
    today = today or date.today()
    report = Report()
    items = list(
        deposit.items.active().select_related(
            "sample__collection_event", "sequencing_run__instrument", "sequencing_run__library"
        )
    )
    samples = [item for item in items if item.kind == ItemKind.SAMPLE]
    reads = [item for item in items if item.kind == ItemKind.READS]
    if not deposit.title.strip():
        report.errors.append(Finding(_("The deposit needs a title.")))
    if not samples or not reads:
        report.errors.append(Finding(_("The deposit needs at least one sample and its reads.")))
    if deposit.environment == Environment.PRODUCTION and (
        deposit.hold_until is None or deposit.hold_until <= today
    ):
        report.errors.append(Finding(_("Give a release date in the future.")))
    for item in samples:
        _check_sample(item, report)
    for item in reads:
        _check_reads(item, report)
        _check_public(item, deposit, report)
    if deposit.archive == Archive.NCBI:
        _check_ncbi(samples, reads, report)
    bioproject = known_bioproject(deposit.project)
    if bioproject:
        message = _("The project already has the BioProject %(accession)s: is a new study wanted?")
        _say(report.warnings, message, accession=bioproject)
    return report


def _say(findings: list, message: str, item: DepositItem | None = None, **values) -> None:
    findings.append(Finding(message % values if values else message, item))


def _check_sample(item: DepositItem, report: Report) -> None:
    sample = item.sample
    name = sample.label or sample.identifier
    event = sample.collection_event
    lacking = []
    if event is None or event.collected_on is None:
        lacking.append(_("the collection date"))
    if event is None or not event.country:
        lacking.append(_("the country"))
    if not sample.host_taxon or not sample.host_tax_id:
        lacking.append(_("the host species and its NCBI taxon ID"))
    for what in lacking:
        _say(report.errors, _("%(sample)s lacks %(what)s."), item, sample=name, what=what)
    if event is None or event.latitude is None or event.longitude is None:
        return
    finest = max(_decimals(event.latitude), _decimals(event.longitude))
    if finest > COORDINATE_DECIMALS_PUBLIC:
        message = _(
            "%(sample)s gives its coordinates to %(places)s decimals: farm locations become public."
        )
        _say(report.warnings, message, item, sample=name, places=finest)


def _check_reads(item: DepositItem, report: Report) -> None:
    run = item.sequencing_run
    name = item.sample.label or item.sample.identifier
    files = list(item.files.active().select_related("file__backend"))
    if not files:
        _say(report.errors, _("%(sample)s has no reads file."), item, sample=name)
    if len({row.file.kind for row in files}) > 1:
        _say(report.errors, _("%(sample)s mixes FASTQ and BAM files."), item, sample=name)
    for row in files:
        problem = None
        if row.file.is_voided:
            problem = _("is voided")
        elif not row.file.backend.is_readable:
            problem = _("is on an offline medium")
        elif row.file.integrity in DAMAGED:
            problem = _("has changed or is missing")
        if problem:
            _say(
                report.errors, _("%(file)s %(problem)s."), item, file=row.file.name, problem=problem
            )
    if run.status != RunStatus.COMPLETED:
        _say(report.errors, _("The run of %(sample)s is not completed."), item, sample=name)
    if run.instrument is None or ena_instrument(run.instrument.model) is None:
        message = _("The instrument of the run of %(sample)s is not known to the archive.")
        _say(report.errors, message, item, sample=name)
    if run.reconstructed:
        message = _("The run of %(sample)s was reconstructed from a record.")
        _say(report.warnings, message, item, sample=name)


def _check_public(item: DepositItem, deposit: Deposit, report: Report) -> None:
    """Reads that are already in a public archive are not deposited again: that is an error for
    the real deposit and a warning for a test, which is discarded. GenBank sequences made from
    the sample are only mentioned: the reads complement them."""
    name = item.sample.label or item.sample.identifier
    known = known_for(root_sample(item.sample))
    if known.archived_reads:
        runs = ", ".join(known.sra_runs or known.sra_experiments)
        message = _(
            "The reads of %(sample)s are already in SRA (%(runs)s): do not send them again."
        )
        _say(
            report.errors if deposit.is_real else report.warnings,
            message,
            item,
            sample=name,
            runs=runs,
        )
    if known.genbank:
        message = _("%(sample)s has sequences in GenBank (%(accessions)s).")
        _say(report.warnings, message, item, sample=name, accessions=", ".join(known.genbank))


# --- NCBI Submission Portal ------------------------------------------------------------------


def ncbi_sample(item: DepositItem, bioproject: str = "") -> ncbi.NcbiSample:
    """The BioSample row of a field sample, in the package the laboratory already uses. The
    collector's name is not part of it."""
    sample = item.sample
    event = sample.collection_event
    return ncbi.NcbiSample(
        name=item.alias,
        title=sample.label or sample.identifier,
        organism=settings.NCBI["ORGANISM"],
        isolate=sample.label or sample.identifier,
        host=sample.host_taxon,
        collection_date=event.collected_on.isoformat() if event and event.collected_on else "",
        geo_loc_name=ncbi.geo_loc_name(
            event.country if event else "",
            *((event.province or event.region, event.locality) if event else ()),
        ),
        lat_lon=(
            ncbi.lat_lon(event.latitude, event.longitude)
            if event and event.latitude is not None and event.longitude is not None
            else ""
        ),
        bioproject_accession=bioproject,
    )


def ncbi_reads(item: DepositItem, bioproject: str = "") -> ncbi.NcbiReads:
    """The SRA row of the reads of a sample; the BioSample is cited by accession when it exists."""
    deposit, run = item.deposit, item.sequencing_run
    files = [row.file for row in item.files.active().select_related("file").order_by("file__path")]
    instrument = ena_instrument(run.instrument.model) if run.instrument else None
    sample = item.sample
    reference = item.sample_item
    return ncbi.NcbiReads(
        library_id=item.alias,
        title=f"{deposit.title}: {sample.label or sample.identifier}",
        sample_name="" if reference.accession else reference.alias,
        biosample_accession=reference.accession,
        instrument_model=instrument or "",
        library_strategy=deposit.library_strategy,
        library_source=deposit.library_source,
        library_selection=deposit.library_selection,
        design_description=f"{run.library.kit.code} library of {sample.get_kind_display().lower()}",
        filetype=files[0].kind if files else "fastq",
        file_names=tuple(file.name for file in files),
        bioproject_accession=bioproject,
    )


def _check_ncbi(samples: list[DepositItem], reads: list[DepositItem], report: Report) -> None:
    """What the portal asks on top of the general checks: coordinates, and file names that are
    unique in the whole submission."""
    for item in samples:
        if item.status != ItemStatus.ACCEPTED and not ncbi_sample(item).lat_lon:
            name = item.sample.label or item.sample.identifier
            message = _("%(sample)s has no coordinates, which NCBI requires.")
            _say(report.errors, message, item, sample=name)
    seen: dict[str, str] = {}
    for item in reads:
        for row in item.files.active().select_related("file"):
            if row.file.name in seen:
                message = _("%(file)s is named like a file of %(other)s: names must be unique.")
                _say(report.errors, message, item, file=row.file.name, other=seen[row.file.name])
            seen[row.file.name] = item.sample.label or item.sample.identifier
