# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guess what a file is from its name; the person registering it can always say otherwise."""

from .models import FileKind

_SUFFIXES = {
    ".pod5": FileKind.POD5,
    ".fast5": FileKind.FAST5,
    ".fastq": FileKind.FASTQ,
    ".fq": FileKind.FASTQ,
    ".bam": FileKind.BAM,
    ".pdf": FileKind.REPORT,
    ".html": FileKind.REPORT,
    ".md": FileKind.REPORT,
    ".jpg": FileKind.GEL_IMAGE,
    ".jpeg": FileKind.GEL_IMAGE,
    ".png": FileKind.GEL_IMAGE,
    ".tif": FileKind.GEL_IMAGE,
    ".tiff": FileKind.GEL_IMAGE,
}


def classify(path: str) -> str:
    """``reads.fastq.gz`` is FASTQ, ``Gel1.jpg`` a gel image, anything unknown ``other``."""
    name = path.rsplit("/", 1)[-1].lower().removesuffix(".gz")
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return _SUFFIXES.get(suffix, FileKind.OTHER)
