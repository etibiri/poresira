# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is prepared for the NCBI Submission Portal, as tables. Pure functions: no database.

NCBI offers no open service PoreSira can send to, and its programmatic route needs a file-transfer
account that NCBI creates. So PoreSira prepares the two tables the portal asks for (BioSample
attributes and SRA metadata) and the list of files to upload; a person submits them in the portal
and records the accessions NCBI gives (``import_public_accessions``).

The BioSample columns follow the package ``MIMS.me.plant-associated.6.0``, the one the
laboratory's existing BioSamples use, with the mandatory attributes of NCBI's package page. The
SRA metadata columns are kept in ``SRA_COLUMNS``: check them against the template NCBI gives
you, which is the reference, and edit that one constant if a name differs.
"""

from dataclasses import dataclass, field
from decimal import Decimal

PACKAGE = "MIMS.me.plant-associated.6.0"
NOT_APPLICABLE = "not applicable"  # as in the laboratory's accepted BioSamples
BIOSAMPLE_COLUMNS = (
    "sample_name",
    "sample_title",
    "bioproject_accession",
    "organism",
    "isolate",
    "host",
    "collection_date",
    "env_broad_scale",
    "env_local_scale",
    "env_medium",
    "geo_loc_name",
    "lat_lon",
)
SRA_COLUMNS = (
    "bioproject_accession",
    "biosample_accession",
    "sample_name",
    "library_ID",
    "title",
    "library_strategy",
    "library_source",
    "library_selection",
    "library_layout",
    "platform",
    "instrument_model",
    "design_description",
    "filetype",
)  # followed by filename, filename2, ... as many as the experiment with most files needs
SRA_PLATFORM = "OXFORD_NANOPORE"


@dataclass(frozen=True)
class NcbiSample:
    name: str  # unique in the account
    title: str
    organism: str  # a metagenome, as the package requires
    isolate: str
    host: str
    collection_date: str
    geo_loc_name: str
    lat_lon: str
    bioproject_accession: str = ""
    env_broad_scale: str = NOT_APPLICABLE
    env_local_scale: str = NOT_APPLICABLE
    env_medium: str = NOT_APPLICABLE


@dataclass(frozen=True)
class NcbiReads:
    library_id: str  # unique in the submission
    title: str
    sample_name: str  # the sample just created in the portal, or ...
    biosample_accession: str  # ... a BioSample that already exists
    instrument_model: str
    library_strategy: str
    library_source: str
    library_selection: str
    design_description: str
    filetype: str  # fastq or bam
    file_names: tuple[str, ...] = field(default_factory=tuple)
    bioproject_accession: str = ""
    library_layout: str = "single"


def lat_lon(latitude: Decimal, longitude: Decimal) -> str:
    """``11.08 N 0.90 W``: degrees and a hemisphere, as the package asks."""

    def part(value: Decimal, positive: str, negative: str) -> str:
        degrees = abs(value).normalize()
        if degrees.as_tuple().exponent > -2:  # two decimals at least, as the laboratory writes
            degrees = degrees.quantize(Decimal("0.01"))
        return f"{format(degrees, 'f')} {positive if value >= 0 else negative}"

    return f"{part(latitude, 'N', 'S')} {part(longitude, 'E', 'W')}"


def geo_loc_name(country: str, *places: str) -> str:
    """``Burkina Faso: Nahouri, Tiebele``: the country, a colon, then the places."""
    detail = ", ".join(place for place in places if place)
    return f"{country}: {detail}" if detail else country


def _row(values: list[str]) -> str:
    # a tab or a line break inside a value would shift the columns
    return "\t".join(" ".join(value.split()) for value in values) + "\n"


def biosample_table(samples: list[NcbiSample]) -> str:
    lines = [_row(list(BIOSAMPLE_COLUMNS))]
    for sample in samples:
        lines.append(
            _row(
                [
                    sample.name,
                    sample.title,
                    sample.bioproject_accession,
                    sample.organism,
                    sample.isolate,
                    sample.host,
                    sample.collection_date,
                    sample.env_broad_scale,
                    sample.env_local_scale,
                    sample.env_medium,
                    sample.geo_loc_name,
                    sample.lat_lon,
                ]
            )
        )
    return "".join(lines)


def sra_table(experiments: list[NcbiReads]) -> str:
    widest = max((len(e.file_names) for e in experiments), default=1)
    names = ["filename"] + [f"filename{number}" for number in range(2, widest + 1)]
    lines = [_row([*SRA_COLUMNS, *names])]
    for e in experiments:
        files = list(e.file_names) + [""] * (widest - len(e.file_names))
        lines.append(
            _row(
                [
                    e.bioproject_accession,
                    e.biosample_accession,
                    e.sample_name,
                    e.library_id,
                    e.title,
                    e.library_strategy,
                    e.library_source,
                    e.library_selection,
                    e.library_layout,
                    SRA_PLATFORM,
                    e.instrument_model,
                    e.design_description,
                    e.filetype,
                    *files,
                ]
            )
        )
    return "".join(lines)


def upload_list(paths: list[str]) -> str:
    """One path per line: the files to send to NCBI (by the portal's upload, FTP or Aspera)."""
    return "".join(f"{path}\n" for path in paths)


def instructions(identifier: str, has_samples: bool) -> str:
    steps = [
        f"PoreSira deposit {identifier}: files for the NCBI Submission Portal.",
        "",
        "PoreSira does not send this deposit. A person submits it, then records what NCBI gives.",
        "",
        "1. Sign in to the Submission Portal (https://submit.ncbi.nlm.nih.gov/) and start an SRA"
        " submission.",
    ]
    if has_samples:
        steps.append(
            "2. Create the BioSamples from biosample_attributes.tsv, with the package"
            f" {PACKAGE}. Copy the columns into NCBI's template (the names are NCBI's;"
            " the template marks the mandatory ones) or paste them in the portal's sheet."
        )
    steps += [
        "3. Fill the SRA metadata template from sra_metadata.tsv in the same way.",
        "4. Upload the files listed in files_to_upload.txt. File names must be unique in a"
        " submission; compress with gzip or bzip2, never zip.",
        "5. When NCBI answers, record the accessions in PoreSira so that no later deposit sends"
        " these data twice:",
        "   python manage.py import_public_accessions accessions.csv --project CODE --user NAME",
        "   (columns: sample, kind, accession, released_on, notes).",
        "",
        "The SRA column names were written from NCBI's documentation but not checked against the"
        " template NCBI issues today; the template is the reference.",
    ]
    return "\n".join(steps) + "\n"
