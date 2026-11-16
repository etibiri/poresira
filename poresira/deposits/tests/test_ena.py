# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: S314 - the XML parsed here was written by the code under test
import xml.etree.ElementTree as ET
from datetime import date

import pytest

from poresira.deposits.ena import (
    Attribute,
    ReadFile,
    ReadsSpec,
    SampleSpec,
    StudySpec,
    ena_instrument,
    reads_manifest,
    samples_xml,
    study_xml,
    submission_xml,
)


class TestInstrument:
    @pytest.mark.parametrize(
        ("model", "name"),
        [
            ("MinION Mk1C", "MinION"),
            ("MinION Mk1B", "MinION"),
            ("PromethION 2 Solo", "PromethION"),
            ("GridION X5", "GridION"),
            ("Flongle adapter", None),
            ("", None),
        ],
    )
    def test_the_instrument_model_is_translated_to_the_archive_name(self, model, name):
        assert ena_instrument(model) == name


class TestXml:
    def test_the_study_is_a_project_set_with_one_sequencing_project(self):
        xml = study_xml(StudySpec("dep_study", "Sweetpotato viromes", "RCA reads"))

        root = ET.fromstring(xml)
        project = root.find("PROJECT")
        assert root.tag == "PROJECT_SET" and project.get("alias") == "dep_study"
        assert project.findtext("TITLE") == "Sweetpotato viromes"
        assert project.findtext("DESCRIPTION") == "RCA reads"
        assert project.find("SUBMISSION_PROJECT/SEQUENCING_PROJECT") is not None
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')

    def test_samples_carry_their_attributes_and_the_checklist(self):
        spec = SampleSpec(
            alias="dep_S1",
            title="Tie_BFA316",
            scientific_name="Ipomoea batatas",
            tax_id=4120,
            attributes=(
                Attribute("collection date", "2016-04-16"),
                Attribute("geographic location (latitude)", "11.08", "DD"),
            ),
        )

        root = ET.fromstring(samples_xml([spec], "ERC000011"))

        sample = root.find("SAMPLE")
        assert sample.get("alias") == "dep_S1" and sample.findtext("TITLE") == "Tie_BFA316"
        assert sample.findtext("SAMPLE_NAME/TAXON_ID") == "4120"
        assert sample.findtext("SAMPLE_NAME/SCIENTIFIC_NAME") == "Ipomoea batatas"
        values = {
            a.findtext("TAG"): (a.findtext("VALUE"), a.findtext("UNITS"))
            for a in sample.findall("SAMPLE_ATTRIBUTES/SAMPLE_ATTRIBUTE")
        }
        assert values["collection date"] == ("2016-04-16", None)
        assert values["geographic location (latitude)"] == ("11.08", "DD")
        assert values["ENA-CHECKLIST"] == ("ERC000011", None)

    def test_special_characters_are_escaped_not_interpreted(self):
        spec = SampleSpec("a", "<b>&\"'", "Ipomoea & co", 1, (Attribute("note", "x</VALUE><y/>"),))

        root = ET.fromstring(samples_xml([spec], "ERC000011"))

        assert root.find("SAMPLE").findtext("TITLE") == "<b>&\"'"
        assert root.find("SAMPLE/SAMPLE_ATTRIBUTES/SAMPLE_ATTRIBUTE/VALUE").text == "x</VALUE><y/>"
        assert root.find(".//y") is None

    def test_the_submission_registers_and_holds_the_data_until_a_date(self):
        root = ET.fromstring(submission_xml(date(2028, 1, 1)))

        assert root.find("ACTIONS/ACTION/ADD") is not None
        assert root.find("ACTIONS/ACTION/HOLD").get("HoldUntilDate") == "2028-01-01"
        assert ET.fromstring(submission_xml()).find("ACTIONS/ACTION/HOLD") is None


class TestManifest:
    def spec(self, **attributes):
        defaults = {
            "name": "dep_reads_1",
            "study": "PRJEB12345",
            "sample": "SAMEA123456",
            "instrument": "MinION",
            "library_source": "GENOMIC",
            "library_selection": "RANDOM",
            "library_strategy": "WGS",
            "files": (ReadFile("fastq", "/data/run/barcode01/a.fastq.gz"),),
        }
        return ReadsSpec(**{**defaults, **attributes})

    def test_each_field_is_a_line_with_its_value(self):
        manifest = reads_manifest(self.spec(library_name="RCA 1", description="SQK on FLO"))

        lines = [line.split("\t") for line in manifest.splitlines()]
        assert lines == [
            ["STUDY", "PRJEB12345"],
            ["SAMPLE", "SAMEA123456"],
            ["NAME", "dep_reads_1"],
            ["INSTRUMENT", "MinION"],
            ["LIBRARY_SOURCE", "GENOMIC"],
            ["LIBRARY_SELECTION", "RANDOM"],
            ["LIBRARY_STRATEGY", "WGS"],
            ["LIBRARY_NAME", "RCA 1"],
            ["DESCRIPTION", "SQK on FLO"],
            ["FASTQ", "/data/run/barcode01/a.fastq.gz"],
        ]

    def test_several_files_and_bam_files_are_listed_one_per_line(self):
        manifest = reads_manifest(
            self.spec(files=(ReadFile("bam", "/d/a.bam"), ReadFile("bam", "/d/b.bam")))
        )

        assert manifest.count("BAM\t") == 2 and "FASTQ" not in manifest

    def test_a_description_cannot_break_the_layout(self):
        manifest = reads_manifest(self.spec(description="line one\nSTUDY\tPRJEB666\n\tx"))

        assert manifest.count("STUDY\t") == 1
        assert len(manifest.splitlines()) == 9
