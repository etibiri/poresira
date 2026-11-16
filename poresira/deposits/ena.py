# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is sent to the European Nucleotide Archive, as text. Pure functions: no database, no
network. The archive's own validation (``webin-cli -validate``) has the last word.

Study, samples and the submission action are XML; reads go through a Webin-CLI manifest.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date

CHECKLIST_TAG = "ENA-CHECKLIST"
XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'
# Words of the instrument model, as PoreSira writes it, and the archive's name for it.
INSTRUMENTS = (("promethion", "PromethION"), ("gridion", "GridION"), ("minion", "MinION"))


@dataclass(frozen=True)
class StudySpec:
    alias: str
    title: str
    description: str = ""


@dataclass(frozen=True)
class Attribute:
    tag: str
    value: str
    units: str = ""


@dataclass(frozen=True)
class SampleSpec:
    alias: str
    title: str
    scientific_name: str
    tax_id: int
    attributes: tuple[Attribute, ...] = field(default_factory=tuple)
    description: str = ""


@dataclass(frozen=True)
class ReadFile:
    kind: str  # "fastq" or "bam"
    path: str  # where the agent finds it


@dataclass(frozen=True)
class ReadsSpec:
    name: str
    study: str  # accession of the study, known once it is registered
    sample: str  # accession of the sample
    instrument: str
    library_source: str
    library_selection: str
    library_strategy: str
    files: tuple[ReadFile, ...]
    library_name: str = ""
    description: str = ""


def ena_instrument(model: str) -> str | None:
    """``MinION Mk1C`` is ``MinION``; a model the archive does not list gives ``None``."""
    lowered = model.lower()
    return next((name for word, name in INSTRUMENTS if word in lowered), None)


def _text(parent: ET.Element, tag: str, value: str) -> ET.Element:
    element = ET.SubElement(parent, tag)
    element.text = value
    return element


def _serialize(root: ET.Element) -> str:
    ET.indent(root)
    return XML_HEADER + ET.tostring(root, encoding="unicode") + "\n"


def study_xml(study: StudySpec) -> str:
    root = ET.Element("PROJECT_SET")
    project = ET.SubElement(root, "PROJECT", alias=study.alias)
    _text(project, "NAME", study.title)
    _text(project, "TITLE", study.title)
    _text(project, "DESCRIPTION", study.description or study.title)
    ET.SubElement(ET.SubElement(project, "SUBMISSION_PROJECT"), "SEQUENCING_PROJECT")
    return _serialize(root)


def samples_xml(samples: list[SampleSpec], checklist: str) -> str:
    root = ET.Element("SAMPLE_SET")
    for spec in samples:
        sample = ET.SubElement(root, "SAMPLE", alias=spec.alias)
        _text(sample, "TITLE", spec.title)
        name = ET.SubElement(sample, "SAMPLE_NAME")
        _text(name, "TAXON_ID", str(spec.tax_id))
        _text(name, "SCIENTIFIC_NAME", spec.scientific_name)
        if spec.description:
            _text(sample, "DESCRIPTION", spec.description)
        attributes = ET.SubElement(sample, "SAMPLE_ATTRIBUTES")
        for attribute in (*spec.attributes, Attribute(CHECKLIST_TAG, checklist)):
            entry = ET.SubElement(attributes, "SAMPLE_ATTRIBUTE")
            _text(entry, "TAG", attribute.tag)
            _text(entry, "VALUE", attribute.value)
            if attribute.units:
                _text(entry, "UNITS", attribute.units)
    return _serialize(root)


def submission_xml(hold_until: date | None = None) -> str:
    """The action that registers the objects, with the date they stay private until."""
    root = ET.Element("SUBMISSION")
    actions = ET.SubElement(root, "ACTIONS")
    ET.SubElement(ET.SubElement(actions, "ACTION"), "ADD")
    if hold_until is not None:
        ET.SubElement(
            ET.SubElement(actions, "ACTION"), "HOLD", HoldUntilDate=hold_until.isoformat()
        )
    return _serialize(root)


def reads_manifest(reads: ReadsSpec) -> str:
    """A Webin-CLI manifest for ``-context reads``: one field per line, name then value."""
    lines = [
        ("STUDY", reads.study),
        ("SAMPLE", reads.sample),
        ("NAME", reads.name),
        ("INSTRUMENT", reads.instrument),
        ("LIBRARY_SOURCE", reads.library_source),
        ("LIBRARY_SELECTION", reads.library_selection),
        ("LIBRARY_STRATEGY", reads.library_strategy),
    ]
    if reads.library_name:
        lines.append(("LIBRARY_NAME", reads.library_name))
    if reads.description:
        lines.append(("DESCRIPTION", " ".join(reads.description.split())))
    lines += [(file.kind.upper(), file.path) for file in reads.files]
    return "".join(f"{name}\t{value}\n" for name, value in lines)
