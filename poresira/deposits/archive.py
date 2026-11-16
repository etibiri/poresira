# SPDX-License-Identifier: AGPL-3.0-or-later
"""Talk to the European Nucleotide Archive. Only the agent, never the web server, calls this.

The study and samples go through the Webin submission service as XML; reads go through
Webin-CLI, which also uploads the files. The Webin password is read from the settings when it is
needed and is never written to the database or to a log: every text that leaves this module
has it removed.
"""

import re
import shlex
import subprocess
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings

from .models import Environment

MAX_RESPONSE_BYTES = 2_000_000
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


class ArchiveError(Exception):
    """The archive could not be reached or gave an answer that cannot be used."""


@dataclass(frozen=True)
class Registered:
    accession: str
    external: tuple[tuple[str, str], ...] = ()  # (type, accession) given alongside


@dataclass
class Receipt:
    success: bool
    objects: dict[tuple[str, str], Registered] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    text: str = ""

    def first(self, tag: str, prefix: str = "") -> str:
        """The accession of the first object of a kind, whatever its alias (Webin-CLI names
        the run and the experiment itself)."""
        for (kind, _alias), registered in self.objects.items():
            if kind == tag and registered.accession.startswith(prefix):
                return registered.accession
        return ""

    def accession_of(self, tag: str, alias: str, prefix: str = "") -> str:
        """The accession of an object, preferring the external accession that starts with
        ``prefix`` (a study is ``PRJEB…`` next to its older ``ERP…`` number)."""
        registered = self.objects.get((tag, alias))
        if registered is None:
            return ""
        for _kind, accession in registered.external:
            if prefix and accession.startswith(prefix):
                return accession
        return registered.accession if not prefix or registered.accession.startswith(prefix) else ""


def scrub(text: str, *secrets: str) -> str:
    """The text without any secret in it."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def parse_receipt(text: str) -> Receipt:
    """Read a receipt of the submission service or of Webin-CLI."""
    if len(text) > MAX_RESPONSE_BYTES or re.search(r"<!(DOCTYPE|ENTITY)", text, re.IGNORECASE):
        raise ArchiveError("The archive's answer is not a plain receipt.")
    try:
        root = ET.fromstring(text)  # noqa: S314 - DOCTYPE and ENTITY were refused above
    except ET.ParseError as error:
        raise ArchiveError("The archive's answer is not XML.") from error
    receipt = Receipt(success=root.get("success") == "true", text=text)
    for element in root:
        accession, alias = element.get("accession"), element.get("alias")
        if accession and alias:
            external = tuple(
                (child.get("type", ""), child.get("accession", ""))
                for child in element.findall("EXT_ID")
            )
            receipt.objects[(element.tag, alias)] = Registered(accession, external)
    receipt.errors = [m.text or "" for m in root.findall("MESSAGES/ERROR")]
    receipt.infos = [m.text or "" for m in root.findall("MESSAGES/INFO")]
    return receipt


def _check_url(url: str) -> str:
    """Credentials only travel over HTTPS, except to this computer (for tests)."""
    parsed = urlparse(url)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS):
        return url
    raise ArchiveError("The archive address must be https.")


class EnaClient:
    """The Webin submission service: study and samples as XML in one multipart request."""

    def __init__(self, url: str, user: str, password: str, timeout: int = 120):
        self.url = _check_url(url)
        self.user, self.password, self.timeout = user, password, timeout

    def _multipart(self, files: dict[str, str]) -> tuple[bytes, str]:
        boundary = f"----poresira{uuid.uuid4().hex}"
        parts = []
        for name, content in files.items():
            parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{name.lower()}.xml"\r\n'
                "Content-Type: text/xml\r\n\r\n"
                f"{content}\r\n"
            )
        body = ("".join(parts) + f"--{boundary}--\r\n").encode()
        return body, f"multipart/form-data; boundary={boundary}"

    def submit(self, files: dict[str, str]) -> Receipt:
        body, content_type = self._multipart(files)
        credentials = b64encode(f"{self.user}:{self.password}".encode()).decode()
        request = urllib.request.Request(  # noqa: S310 - the scheme is checked above
            self.url,
            data=body,
            headers={"Authorization": f"Basic {credentials}", "Content-Type": content_type},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                answer = response.read(MAX_RESPONSE_BYTES + 1).decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            raise ArchiveError(f"The archive answered HTTP {error.code}.") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ArchiveError(f"The archive could not be reached: {error}") from error
        return parse_receipt(answer)


class WebinCli:
    """Webin-CLI, which validates reads, uploads their files and registers them.

    Its only documented way to take the password is the ``-password`` option, so the password is
    visible in the process list of this computer while the command runs. Run the agent on a
    machine you control, with an account made for PoreSira.
    """

    def __init__(self, command: str, user: str, password: str, timeout: int, test: bool):
        self.command = shlex.split(command)
        self.user, self.password, self.timeout, self.test = user, password, timeout, test

    def submit_reads(self, manifest: str, name: str, directory: Path) -> Receipt:
        directory.mkdir(parents=True, exist_ok=True)
        manifest_path = directory / "manifest.txt"
        manifest_path.write_text(manifest)
        command = [
            *self.command,
            "-context", "reads",
            "-userName", self.user,
            "-password", self.password,
            "-manifest", str(manifest_path),
            "-outputDir", str(directory),
            "-submit",
        ]  # fmt: skip
        if self.test:
            command.append("-test")
        try:
            done = subprocess.run(  # noqa: S603
                command, capture_output=True, text=True, timeout=self.timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ArchiveError(
                scrub(f"Webin-CLI could not be run: {error}", self.password)
            ) from error
        output = scrub(f"{done.stdout}\n{done.stderr}".strip(), self.password)
        receipt = self._read_receipt(directory / "reads" / name / "submit")
        if done.returncode != 0 or receipt is None:
            raise ArchiveError(f"Webin-CLI did not submit the reads:\n{output[-2000:]}")
        return receipt

    @staticmethod
    def _read_receipt(folder: Path) -> Receipt | None:
        """The receipt Webin-CLI leaves in its output folder, whatever it is called."""
        for path in sorted(folder.glob("*.xml")) if folder.is_dir() else []:
            text = path.read_text(errors="replace")
            if "<RECEIPT" in text:
                return parse_receipt(text)
        return None


class EnaArchive:
    """What a deposit needs from the archive, for the test or the production service."""

    def __init__(self, environment: str, config: dict | None = None):
        config = config or settings.ENA
        self.environment = environment
        self.user, self.password = config["WEBIN_USER"], config["WEBIN_PASSWORD"]
        if not self.user or not self.password:
            raise ArchiveError("The Webin account is not configured (ENA_WEBIN_USER and PASSWORD).")
        url = config["PRODUCTION_URL" if environment == Environment.PRODUCTION else "TEST_URL"]
        self.client = EnaClient(url, self.user, self.password, config["TIMEOUT_SECONDS"])
        self.webin = WebinCli(
            config["WEBIN_CLI"],
            self.user,
            self.password,
            config["CLI_TIMEOUT_SECONDS"],
            test=environment != Environment.PRODUCTION,
        )
        self.work_dir = Path(config["WORK_DIR"])

    def register(self, study: str | None, samples: str | None, submission: str) -> Receipt:
        """Register the study and the samples that are given, in one request."""
        files = {"SUBMISSION": submission}
        if study:
            files["PROJECT"] = study
        if samples:
            files["SAMPLE"] = samples
        return self.client.submit(files)

    def submit_reads(self, manifest: str, name: str, deposit_identifier: str) -> Receipt:
        return self.webin.submit_reads(manifest, name, self.work_dir / deposit_identifier / name)

    def scrub(self, text: str) -> str:
        return scrub(text, self.password)
