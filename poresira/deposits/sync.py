# SPDX-License-Identifier: AGPL-3.0-or-later
"""Link PoreSira to the public archives: record the accessions they hold for a project and
report where their records and PoreSira's differ.

Reading only: the archives are never written to, and no sample, sequence or BioSample is changed.
By default nothing is recorded either (a dry run); ``apply`` records the new accessions.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction

from poresira.core.models import Project
from poresira.samples.models import Sample, SampleKind, SequenceRecord

from . import services
from .collect import biological_sample
from .known import known_for
from .models import AccessionKind, PublicAccession
from .public_sync import (
    ArchiveReader,
    Ena,
    Ncbi,
    PublicArchiveError,
    SraRun,
    parse_lat_lon,
)

BIOLOGICAL = (SampleKind.FIELD_SAMPLE, SampleKind.LIVING_PLANT)
COORDINATE_TOLERANCE = Decimal("0.005")


@dataclass
class SyncReport:
    source: str = ""
    bioproject: str = ""
    would_record: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # kind, accession, sample
    recorded: list[PublicAccession] = field(default_factory=list)
    already_known: int = 0
    unmatched_runs: list[SraRun] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    genbank_missing: list[str] = field(default_factory=list)
    genbank_differences: list[str] = field(default_factory=list)
    biosample_differences: list[str] = field(default_factory=list)
    sequenced_without_sra: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_differences(self) -> bool:
        return bool(
            self.unmatched_runs
            or self.conflicts
            or self.genbank_missing
            or self.genbank_differences
            or self.biosample_differences
            or self.sequenced_without_sra
        )


def _name(sample: Sample) -> str:
    return sample.label or sample.identifier


def _samples_by_biosample_and_label(project: Project):
    """The biological samples of the project, found by a BioSample they already have or by label
    (a field sample wins over a plant that carries the same label)."""
    samples = list(
        Sample.objects.active()
        .filter(project=project, kind__in=BIOLOGICAL)
        .select_related("parent")
    )
    by_label: dict[str, Sample] = {}
    for sample in sorted(samples, key=lambda s: s.kind != SampleKind.FIELD_SAMPLE, reverse=True):
        by_label[sample.label] = sample  # field samples come last, so they win
    by_biosample: dict[str, Sample] = {}
    for record in (
        PublicAccession.objects.active()
        .filter(project=project, kind=AccessionKind.BIOSAMPLE)
        .select_related("sample")
    ):
        by_biosample[record.accession] = record.sample
    sequences = (
        SequenceRecord.objects.active()
        .filter(sample__project=project)
        .exclude(biosample_accession="")
    )
    for record in sequences.select_related("sample__parent"):
        by_biosample.setdefault(record.biosample_accession, biological_sample(record.sample))
    return by_biosample, by_label


def _plan(project: Project, bioproject: str, runs: list[SraRun], report: SyncReport):
    by_biosample, by_label = _samples_by_biosample_and_label(project)
    existing = set(
        PublicAccession.objects.active().filter(project=project).values_list("kind", "accession")
    )
    rows: list[tuple[str, str, Sample | None]] = []
    if (AccessionKind.BIOPROJECT, bioproject) not in existing:
        rows.append((AccessionKind.BIOPROJECT, bioproject, None))
    matched: dict[str, Sample] = {}
    for run in runs:
        sample = by_biosample.get(run.biosample) or by_label.get(run.sample_name)
        if sample is None:
            report.unmatched_runs.append(run)
            continue
        matched[run.biosample] = sample
        for kind, accession in (
            (AccessionKind.BIOSAMPLE, run.biosample),
            (AccessionKind.SRA_EXPERIMENT, run.experiment),
            (AccessionKind.SRA_RUN, run.run),
        ):
            if not accession:
                continue
            if (kind, accession) in existing:
                report.already_known += 1
            else:
                rows.append((kind, accession, sample))
                existing.add((kind, accession))
    for kind, accession, sample in rows:
        report.would_record.append((kind, accession, _name(sample) if sample else ""))
    return rows, matched


def _check_genbank(project: Project, ncbi: Ncbi, report: SyncReport) -> None:
    records = list(SequenceRecord.objects.active().filter(sample__project=project))
    if not records:
        return
    summaries = ncbi.genbank(sorted({record.accession.split(".")[0] for record in records}))
    for record in records:
        accession = record.accession.split(".")[0]
        found = summaries.get(accession)
        if found is None:
            report.genbank_missing.append(record.accession)
            continue
        if found.length_bp and found.length_bp != record.length_bp:
            report.genbank_differences.append(
                f"{record.accession}: {found.length_bp} bp at NCBI, "
                f"{record.length_bp} bp in PoreSira."
            )
        if found.isolate and record.isolate and found.isolate != record.isolate:
            report.genbank_differences.append(
                f"{record.accession}: isolate {found.isolate} at NCBI, "
                f"{record.isolate} in PoreSira."
            )


def _check_biosamples(ncbi: Ncbi, matched: dict[str, Sample], report: SyncReport) -> None:
    for accession, record in ncbi.biosamples(sorted(matched)).items():
        sample = matched.get(accession)
        if sample is None:
            continue
        event, attributes, name = sample.collection_event, record.attributes, _name(sample)
        if event and event.collected_on and attributes.get("collection_date"):
            theirs = attributes["collection_date"]
            if not event.collected_on.isoformat().startswith(theirs[:10]) and not theirs.startswith(
                event.collected_on.isoformat()[: len(theirs)]
            ):
                report.biosample_differences.append(
                    f"{name} ({accession}): collection date {theirs} at NCBI, "
                    f"{event.collected_on.isoformat()} in PoreSira."
                )
        host = attributes.get("host", "")
        if host and sample.host_taxon and host.lower() != sample.host_taxon.lower():
            report.biosample_differences.append(
                f"{name} ({accession}): host {host} at NCBI, {sample.host_taxon} in PoreSira."
            )
        point = parse_lat_lon(attributes.get("lat_lon", ""))
        if point and event and event.latitude is not None and event.longitude is not None:
            far = (
                abs(point[0] - event.latitude) > COORDINATE_TOLERANCE
                or abs(point[1] - event.longitude) > COORDINATE_TOLERANCE
            )
            if far:
                report.biosample_differences.append(
                    f"{name} ({accession}): coordinates {attributes['lat_lon']} at NCBI differ "
                    "from PoreSira's."
                )


def sync_public_archives(
    *,
    user,
    project: Project,
    bioproject: str,
    source: str = "auto",
    apply: bool = False,
    ncbi: Ncbi | None = None,
    ena: Ena | None = None,
) -> SyncReport:
    """Read the archives for ``bioproject`` and compare with the project. With ``apply``, record
    the accessions PoreSira lacks; otherwise only say what would be recorded."""
    reader = ArchiveReader()
    ncbi = ncbi or Ncbi(reader)
    ena = ena or Ena(reader)
    report = SyncReport(bioproject=bioproject)
    runs: list[SraRun] | None = None
    for name, archive in (("ncbi", ncbi), ("ena", ena)):
        if source not in ("auto", name):
            continue
        try:
            runs, report.source = archive.sra_runs(bioproject), name
            break
        except PublicArchiveError as error:
            report.errors.append(f"{name.upper()}: {error}")
    if runs is None:
        raise PublicArchiveError("; ".join(report.errors) or "No archive could be read.")
    rows, matched = _plan(project, bioproject, runs, report)
    if apply:
        with transaction.atomic():
            for kind, accession, sample in rows:
                report.recorded.append(
                    services.record_public_accession(
                        user=user, project=project, kind=kind, accession=accession, sample=sample
                    )
                )
    try:
        _check_genbank(project, ncbi, report)
    except PublicArchiveError as error:
        report.errors.append(f"GenBank was not checked: {error}")
    try:
        _check_biosamples(ncbi, matched, report)
    except PublicArchiveError as error:
        report.errors.append(f"BioSamples were not compared: {error}")
    sra_known = {sample.pk for sample in matched.values()} | set(
        PublicAccession.objects.active()
        .filter(project=project, kind=AccessionKind.SRA_RUN)
        .values_list("sample_id", flat=True)
    )
    for record in SequenceRecord.objects.active().filter(sample__project=project):
        biological = biological_sample(record.sample)
        if biological.pk not in sra_known and not known_for(biological).sra_runs:
            report.sequenced_without_sra.append(_name(biological))
    report.sequenced_without_sra = sorted(set(report.sequenced_without_sra))
    return report
