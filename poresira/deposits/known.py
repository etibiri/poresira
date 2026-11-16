# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the public archives already hold for a field sample, from every place PoreSira knows it:
the accessions recorded for it, and the GenBank records of the material derived from it.

The archives exchange their records (an NCBI BioSample can be cited in an ENA submission, and
the reads of an SRA run are in ENA too), so a deposit must not register the same thing again.
"""

from dataclasses import dataclass

from poresira.core.models import Project
from poresira.samples.models import Sample, SequenceRecord

from .models import AccessionKind, PublicAccession

NCBI_BIOSAMPLE = "https://www.ncbi.nlm.nih.gov/biosample/"
NCBI_SRA = "https://www.ncbi.nlm.nih.gov/sra/"
NCBI_NUCCORE = "https://www.ncbi.nlm.nih.gov/nuccore/"
NCBI_BIOPROJECT = "https://www.ncbi.nlm.nih.gov/bioproject/"


@dataclass(frozen=True)
class Known:
    """The public accessions of a field sample. Empty fields mean nothing is known."""

    biosample: str = ""
    sra_runs: tuple[str, ...] = ()
    sra_experiments: tuple[str, ...] = ()
    genbank: tuple[str, ...] = ()

    @property
    def archived_reads(self) -> bool:
        return bool(self.sra_runs or self.sra_experiments)


def family(sample: Sample) -> list[Sample]:
    """The sample and everything derived from it."""
    members, frontier = [sample], [sample]
    while frontier:
        frontier = list(Sample.objects.filter(parent__in=frontier))
        members += frontier
    return members


def known_for(field_sample: Sample) -> Known:
    """Everything public about a field sample: its BioSample, its SRA runs and experiments, and
    the GenBank accessions of the sequences made from it."""
    recorded = PublicAccession.objects.active().filter(sample=field_sample)

    def of_kind(kind: str) -> tuple[str, ...]:
        return tuple(sorted(recorded.filter(kind=kind).values_list("accession", flat=True)))

    sequences = SequenceRecord.objects.active().filter(sample__in=family(field_sample))
    biosamples = of_kind(AccessionKind.BIOSAMPLE)
    biosample = biosamples[0] if biosamples else ""
    if not biosample:  # the BioSample is also written on the sequences made from the sample
        cited = sequences.exclude(biosample_accession="").values_list(
            "biosample_accession", flat=True
        )
        biosample = next(iter(sorted(set(cited))), "")
    return Known(
        biosample=biosample,
        sra_runs=of_kind(AccessionKind.SRA_RUN),
        sra_experiments=of_kind(AccessionKind.SRA_EXPERIMENT),
        genbank=tuple(sorted(set(sequences.values_list("accession", flat=True)))),
    )


def known_bioproject(project: Project) -> str:
    """The BioProject already registered for the project, if one is."""
    accession = (
        PublicAccession.objects.active()
        .filter(project=project, kind=AccessionKind.BIOPROJECT)
        .values_list("accession", flat=True)
        .first()
    )
    return accession or ""
