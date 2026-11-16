# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read what the public archives hold, and compare it with PoreSira.

Only public accession numbers leave the computer. PoreSira reads the archives and records new
accessions; it never writes to the archives and never changes a sample, a sequence or a
BioSample: differences are reported for a person to settle.
"""

import csv
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings

MAX_BYTES = 20_000_000
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
BATCH = 100
ENA_RUN_FIELDS = (
    "run_accession",
    "experiment_accession",
    "sample_accession",
    "sample_alias",
    "first_public",
)


class PublicArchiveError(Exception):
    """An archive could not be read."""


@dataclass(frozen=True)
class SraRun:
    run: str
    experiment: str
    biosample: str
    sample_name: str
    released_on: date | None = None


@dataclass(frozen=True)
class GenbankSummary:
    accession: str
    length_bp: int
    title: str
    isolate: str = ""


@dataclass(frozen=True)
class BioSampleRecord:
    accession: str
    attributes: dict[str, str] = field(default_factory=dict)


class ArchiveReader:
    """Gets pages from the archives politely: one request at a time, a pause between requests,
    a few retries when the archive is busy. HTTPS only, except to this computer (for tests)."""

    def __init__(self, config: dict | None = None, sleep=time.sleep, clock=time.monotonic):
        self.config = config or settings.PUBLIC_ARCHIVES
        self._sleep, self._clock = sleep, clock
        self._last = 0.0

    @staticmethod
    def _check(url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS):
            return url
        raise PublicArchiveError("The archive address must be https.")

    def get(self, url: str, params: dict[str, str]) -> str:
        full = f"{self._check(url)}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(  # noqa: S310 - the scheme is checked above
            full, headers={"User-Agent": "poresira (open-source LIMS)"}
        )
        for attempt in range(4):
            wait = self.config["MIN_INTERVAL_SECONDS"] - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
            try:
                with urllib.request.urlopen(  # noqa: S310
                    request, timeout=self.config["TIMEOUT_SECONDS"]
                ) as response:
                    return response.read(MAX_BYTES).decode("utf-8", "replace")
            except urllib.error.HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise PublicArchiveError(f"The archive answered HTTP {error.code}.") from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt == 3:
                    raise PublicArchiveError(
                        f"The archive could not be reached: {error}"
                    ) from error
            self._sleep(2**attempt)
        raise PublicArchiveError("The archive could not be read.")  # pragma: no cover


def _released(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


class Ncbi:
    """NCBI E-utilities: SRA runs of a BioProject, GenBank summaries, BioSamples."""

    def __init__(self, reader: ArchiveReader):
        self.reader, self.base = reader, reader.config["NCBI_URL"].rstrip("/")

    def _get(self, tool: str, **params) -> str:
        if key := self.reader.config.get("NCBI_API_KEY"):
            params["api_key"] = key
        return self.reader.get(f"{self.base}/{tool}.fcgi", {"tool": "poresira", **params})

    def sra_runs(self, bioproject: str) -> list[SraRun]:
        found = json.loads(
            self._get("esearch", db="sra", term=bioproject, retmax="5000", retmode="json")
        )["esearchresult"]
        ids = found.get("idlist", [])
        runs = []
        for start in range(0, len(ids), BATCH):
            text = self._get(
                "efetch",
                db="sra",
                rettype="runinfo",
                retmode="text",
                id=",".join(ids[start : start + BATCH]),
            )
            for row in csv.DictReader(io.StringIO(text)):
                if row.get("Run"):
                    runs.append(
                        SraRun(
                            row["Run"],
                            row.get("Experiment", ""),
                            row.get("BioSample", ""),
                            row.get("SampleName", ""),
                            _released(row.get("ReleaseDate", "")),
                        )
                    )
        return runs

    def genbank(self, accessions: list[str]) -> dict[str, GenbankSummary]:
        """Summaries of GenBank records, by accession without version."""
        summaries = {}
        for start in range(0, len(accessions), BATCH):
            batch = accessions[start : start + BATCH]
            data = json.loads(
                self._get("esummary", db="nuccore", id=",".join(batch), retmode="json")
            ).get("result", {})
            for uid in data.get("uids", []):
                record = data[uid]
                accession = str(record.get("caption", "")).split(".")[0]
                names = str(record.get("subtype", "")).split("|")
                values = str(record.get("subname", "")).split("|")
                subnames = dict(zip(names, values, strict=False))
                summaries[accession] = GenbankSummary(
                    accession=accession,
                    length_bp=int(record.get("slen") or 0),
                    title=str(record.get("title", "")),
                    isolate=subnames.get("isolate", ""),
                )
        return summaries

    def biosamples(self, accessions: list[str]) -> dict[str, BioSampleRecord]:
        records = {}
        for start in range(0, len(accessions), BATCH):
            text = self._get(
                "efetch",
                db="biosample",
                retmode="xml",
                id=",".join(accessions[start : start + BATCH]),
            )
            try:
                root = ET.fromstring(text)  # noqa: S314 - refused below when it holds a DOCTYPE
            except ET.ParseError as error:
                raise PublicArchiveError("NCBI's BioSample answer is not XML.") from error
            if re.search(r"<!(DOCTYPE|ENTITY)", text, re.IGNORECASE):
                raise PublicArchiveError("NCBI's BioSample answer is not a plain record.")
            for sample in root.findall("BioSample"):
                attributes = {
                    a.get("harmonized_name") or a.get("attribute_name", ""): (a.text or "")
                    for a in sample.findall("Attributes/Attribute")
                }
                records[sample.get("accession", "")] = BioSampleRecord(
                    sample.get("accession", ""), attributes
                )
        return records


class Ena:
    """ENA Portal API: the SRA runs of a project are mirrored there."""

    def __init__(self, reader: ArchiveReader):
        self.reader, self.base = reader, reader.config["ENA_URL"].rstrip("/")

    def sra_runs(self, bioproject: str) -> list[SraRun]:
        text = self.reader.get(
            f"{self.base}/search",
            {
                "result": "read_run",
                "query": f'study_accession="{bioproject}"',
                "fields": ",".join(ENA_RUN_FIELDS),
                "format": "tsv",
                "limit": "0",
            },
        )
        return [
            SraRun(
                row["run_accession"],
                row.get("experiment_accession", ""),
                row.get("sample_accession", ""),
                row.get("sample_alias", ""),
                _released(row.get("first_public", "")),
            )
            for row in csv.DictReader(io.StringIO(text), delimiter="\t")
            if row.get("run_accession")
        ]


def parse_lat_lon(text: str) -> tuple[Decimal, Decimal] | None:
    """``11.08 N 0.90 W`` as signed degrees."""
    match = re.fullmatch(r"\s*([\d.]+)\s*([NS])\s+([\d.]+)\s*([EW])\s*", text)
    if not match:
        return None
    try:
        latitude, longitude = Decimal(match.group(1)), Decimal(match.group(3))
    except InvalidOperation:
        return None
    return (
        latitude if match.group(2) == "N" else -latitude,
        longitude if match.group(4) == "E" else -longitude,
    )
