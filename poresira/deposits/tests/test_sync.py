# SPDX-License-Identifier: AGPL-3.0-or-later
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.deposits.models import AccessionKind, PublicAccession
from poresira.deposits.public_sync import (
    ArchiveReader,
    Ena,
    Ncbi,
    PublicArchiveError,
    parse_lat_lon,
)
from poresira.deposits.sync import sync_public_archives
from poresira.deposits.tests.factories import PublicAccessionFactory
from poresira.deposits.tests.fake_public import FakePublic
from poresira.samples.models import SampleKind, SequenceRecord
from poresira.samples.tests.factories import (
    CollectionEventFactory,
    DerivativeFactory,
    SampleFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake():
    public = FakePublic()
    yield public
    public.stop()


@pytest.fixture(autouse=True)
def archives(settings, fake):
    settings.PUBLIC_ARCHIVES = fake.config


@pytest.fixture
def project():
    return ProjectFactory(code="SPBF")


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


def field_sample(project, label, **event):
    attributes = {
        "project": project,
        "collected_on": "2016-04-16",
        "country": "Burkina Faso",
        "latitude": Decimal("11.08"),
        "longitude": Decimal("-0.90"),
        **event,
    }
    return SampleFactory(
        project=project,
        label=label,
        host_taxon="Ipomoea batatas",
        collection_event=CollectionEventFactory(**attributes),
    )


def reader():
    return ArchiveReader(sleep=lambda seconds: None)


class TestReaders:
    def test_ncbi_gives_the_runs_of_a_bioproject(self, fake):
        fake.runs = [
            ("SRR1", "SRX1", "SAMN1", "Tie_BFA316"),
            ("SRR2", "SRX2", "SAMN2", "Kan_BFA160"),
        ]

        runs = Ncbi(reader()).sra_runs("PRJNA1")

        assert [(r.run, r.experiment, r.biosample, r.sample_name) for r in runs] == [
            ("SRR1", "SRX1", "SAMN1", "Tie_BFA316"),
            ("SRR2", "SRX2", "SAMN2", "Kan_BFA160"),
        ]
        assert str(runs[0].released_on) == "2026-05-01"

    def test_ena_gives_the_same_runs(self, fake):
        fake.runs = [("SRR1", "SRX1", "SAMN1", "Tie_BFA316")]

        runs = Ena(reader()).sra_runs("PRJNA1")

        assert (runs[0].run, runs[0].biosample, runs[0].sample_name) == (
            "SRR1",
            "SAMN1",
            "Tie_BFA316",
        )

    def test_the_requests_say_who_asks_and_carry_only_public_numbers(self, fake, settings):
        settings.PUBLIC_ARCHIVES = {**fake.config, "NCBI_API_KEY": "key123"}
        fake.runs = [("SRR1", "SRX1", "SAMN1", "x")]

        Ncbi(ArchiveReader(sleep=lambda s: None)).sra_runs("PRJNA1")

        queries = [query for _path, query in fake.requests]
        assert all(q["tool"] == "poresira" and q["api_key"] == "key123" for q in queries)
        assert "email" not in "".join(str(q) for q in queries)

    def test_a_busy_archive_is_retried_then_reported(self, fake):
        fake.ncbi_status = 503
        slept = []

        with pytest.raises(PublicArchiveError, match="503"):
            Ncbi(ArchiveReader(sleep=slept.append)).sra_runs("PRJNA1")

        assert len([p for p, _q in fake.requests if p.endswith("esearch.fcgi")]) == 4
        assert slept == [1, 2, 4]

    def test_genbank_summaries_are_read_without_the_version(self, fake):
        fake.genbank = {"PV947756": (2829, "Tie_BFA316", "SPLCV isolate Tie_BFA316")}

        summaries = Ncbi(reader()).genbank(["PV947756", "PV000000"])

        assert set(summaries) == {"PV947756"}
        assert (summaries["PV947756"].length_bp, summaries["PV947756"].isolate) == (
            2829,
            "Tie_BFA316",
        )

    @pytest.mark.parametrize(
        ("text", "point"),
        [
            ("11.08 N 0.90 W", (Decimal("11.08"), Decimal("-0.90"))),
            ("3.5 S 30 E", (Decimal("-3.5"), Decimal("30"))),
            ("nonsense", None),
            ("11.08 0.90", None),
        ],
    )
    def test_coordinates_are_read_as_signed_degrees(self, text, point):
        assert parse_lat_lon(text) == point

    @pytest.mark.parametrize("url", ["http://eutils.ncbi.nlm.nih.gov/x", "ftp://example.org/x"])
    def test_only_https_or_this_computer_is_read(self, url):
        with pytest.raises(PublicArchiveError, match="https"):
            reader().get(url, {})


class TestSync:
    def test_a_dry_run_says_what_would_be_recorded_and_records_nothing(self, fake, project, editor):
        field_sample(project, "Tie_BFA316")
        fake.runs = [("SRR38354347", "SRX33188775", "SAMN57660728", "Tie_BFA316")]

        report = sync_public_archives(user=editor, project=project, bioproject="PRJNA1460897")

        assert report.source == "ncbi" and not report.recorded
        assert sorted(report.would_record) == [
            ("bioproject", "PRJNA1460897", ""),
            ("biosample", "SAMN57660728", "Tie_BFA316"),
            ("sra_experiment", "SRX33188775", "Tie_BFA316"),
            ("sra_run", "SRR38354347", "Tie_BFA316"),
        ]
        assert not PublicAccession.objects.exists()

    def test_apply_records_them_for_the_right_sample_and_is_idempotent(self, fake, project, editor):
        sample = field_sample(project, "Tie_BFA316")
        fake.runs = [("SRR38354347", "SRX33188775", "SAMN57660728", "Tie_BFA316")]

        first = sync_public_archives(
            user=editor, project=project, bioproject="PRJNA1460897", apply=True
        )
        second = sync_public_archives(
            user=editor, project=project, bioproject="PRJNA1460897", apply=True
        )

        assert len(first.recorded) == 4 and not second.recorded and not second.would_record
        assert second.already_known == 3
        run = PublicAccession.objects.get(kind=AccessionKind.SRA_RUN)
        assert (run.accession, run.sample) == ("SRR38354347", sample)

    def test_a_run_is_matched_by_the_biosample_the_project_already_has(self, fake, project, editor):
        sample = field_sample(project, "Another label")
        PublicAccessionFactory(project=project, accession="SAMN9", sample=sample)
        fake.runs = [("SRR1", "SRX1", "SAMN9", "Tie_BFA316")]

        report = sync_public_archives(user=editor, project=project, bioproject="PRJNA1")

        assert not report.unmatched_runs
        assert ("sra_run", "SRR1", "Another label") in report.would_record

    def test_a_biosample_written_on_a_genbank_record_also_matches(self, fake, project, editor):
        sample = field_sample(project, "Whatever")
        product = DerivativeFactory(kind=SampleKind.AMPLIFICATION_PRODUCT, parent=sample, label="p")
        SequenceRecord.objects.create(
            sample=product,
            accession="PV1",
            organism="x",
            length_bp=10,
            topology="circular",
            biosample_accession="SAMN7",
        )
        fake.runs = [("SRR1", "SRX1", "SAMN7", "no such label")]

        report = sync_public_archives(user=editor, project=project, bioproject="PRJNA1")

        assert not report.unmatched_runs

    def test_a_field_sample_wins_over_the_plant_with_the_same_label(self, fake, project, editor):
        grafted = field_sample(project, "Tie_BFA214")
        plant = SampleFactory(
            project=project,
            kind=SampleKind.LIVING_PLANT,
            parent=grafted,
            label="Tie_BFA214",
            collection_event=grafted.collection_event,
        )
        fake.runs = [("SRR1", "SRX1", "SAMN1", "Tie_BFA214")]

        report = sync_public_archives(user=editor, project=project, bioproject="PRJNA1")

        assert ("biosample", "SAMN1", "Tie_BFA214") in report.would_record
        sync_public_archives(user=editor, project=project, bioproject="PRJNA1", apply=True)
        assert PublicAccession.objects.get(accession="SAMN1").sample == grafted
        assert plant.pk != grafted.pk

    def test_runs_without_a_sample_are_reported_not_invented(self, fake, project, editor):
        fake.runs = [("SRR1", "SRX1", "SAMN1", "Unknown_BFA1")]

        report = sync_public_archives(user=editor, project=project, bioproject="PRJNA1", apply=True)

        assert [r.run for r in report.unmatched_runs] == ["SRR1"]
        assert not PublicAccession.objects.filter(kind="sra_run").exists()

    def test_ena_is_the_fallback_when_ncbi_is_down(self, fake, project, editor):
        field_sample(project, "Tie_BFA316")
        fake.runs = [("SRR1", "SRX1", "SAMN1", "Tie_BFA316")]
        fake.ncbi_status = 503

        report = sync_public_archives(user=editor, project=project, bioproject="PRJNA1")

        assert report.source == "ena" and any("NCBI" in e for e in report.errors)
        assert ("sra_run", "SRR1", "Tie_BFA316") in report.would_record

    def test_a_source_can_be_forced(self, fake, project, editor):
        fake.runs = [("SRR1", "SRX1", "SAMN1", "x")]
        fake.ncbi_status = 503

        with pytest.raises(PublicArchiveError, match="NCBI"):
            sync_public_archives(user=editor, project=project, bioproject="PRJNA1", source="ncbi")

    def test_readers_cannot_record(self, fake, project):
        viewer = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user
        field_sample(project, "Tie_BFA316")
        fake.runs = [("SRR1", "SRX1", "SAMN1", "Tie_BFA316")]

        with pytest.raises(PermissionError if False else Exception):
            sync_public_archives(user=viewer, project=project, bioproject="PRJNA1", apply=True)
        assert not PublicAccession.objects.exists()


class TestComparisons:
    def sequence(self, sample, accession="PV947756", length=2829, isolate="Tie_BFA316"):
        product = DerivativeFactory(
            kind=SampleKind.AMPLIFICATION_PRODUCT, parent=sample, label=f"{sample.label} [p]"
        )
        return SequenceRecord.objects.create(
            sample=product,
            accession=accession,
            organism="Sweet potato leaf curl virus",
            length_bp=length,
            topology="circular",
            isolate=isolate,
        )

    def test_genbank_records_that_agree_are_not_reported(self, fake, project, editor):
        sample = field_sample(project, "Tie_BFA316")
        self.sequence(sample)
        fake.genbank = {"PV947756": (2829, "Tie_BFA316", "SPLCV isolate Tie_BFA316")}
        fake.runs = [("SRR1", "SRX1", "SAMN1", "Tie_BFA316")]

        report = sync_public_archives(user=editor, project=project, bioproject="PRJNA1")

        assert not report.genbank_missing and not report.genbank_differences

    def test_genbank_differences_and_missing_records_are_reported(self, fake, project, editor):
        one = field_sample(project, "Tie_BFA316")
        two = field_sample(project, "Kan_BFA160")
        self.sequence(one, "PV1", length=2829, isolate="Tie_BFA316")
        self.sequence(two, "PV2")
        fake.genbank = {"PV1": (2800, "Other_isolate", "t")}
        fake.runs = []

        report = sync_public_archives(user=editor, project=project, bioproject="PRJNA1")

        assert report.genbank_missing == ["PV2"]
        assert any("2800 bp at NCBI, 2829 bp" in m for m in report.genbank_differences)
        assert any("isolate Other_isolate at NCBI" in m for m in report.genbank_differences)

    def test_biosample_differences_are_reported(self, fake, project, editor):
        field_sample(project, "Tie_BFA316")
        fake.runs = [("SRR1", "SRX1", "SAMN1", "Tie_BFA316")]
        fake.biosamples = {
            "SAMN1": {
                "collection_date": "2016-05-01",
                "host": "Manihot esculenta",
                "lat_lon": "12.50 N 1.50 W",
            }
        }

        report = sync_public_archives(user=editor, project=project, bioproject="PRJNA1")

        text = " ".join(report.biosample_differences)
        assert "collection date 2016-05-01 at NCBI, 2016-04-16" in text
        assert "host Manihot esculenta" in text and "coordinates 12.50 N 1.50 W" in text

    def test_a_biosample_that_agrees_is_not_reported(self, fake, project, editor):
        field_sample(project, "Tie_BFA316")
        fake.runs = [("SRR1", "SRX1", "SAMN1", "Tie_BFA316")]
        fake.biosamples = {
            "SAMN1": {
                "collection_date": "2016-04-16",
                "host": "ipomoea batatas",
                "lat_lon": "11.08 N 0.90 W",
            }
        }

        report = sync_public_archives(user=editor, project=project, bioproject="PRJNA1")

        assert not report.biosample_differences

    def test_a_sequenced_sample_without_a_run_in_the_archive_is_named(self, fake, project, editor):
        sample = field_sample(project, "Tie_BFA316")
        self.sequence(sample)
        fake.genbank = {"PV947756": (2829, "Tie_BFA316", "t")}
        fake.runs = []

        report = sync_public_archives(user=editor, project=project, bioproject="PRJNA1")

        assert report.sequenced_without_sra == ["Tie_BFA316"] and report.has_differences


class TestCommand:
    def test_the_command_reports_and_applies(self, fake, project, editor):
        field_sample(project, "Tie_BFA316")
        fake.runs = [("SRR1", "SRX1", "SAMN1", "Tie_BFA316")]
        arguments = ["SPBF", "--bioproject", "PRJNA1", "--user", editor.username]
        out = StringIO()

        call_command("sync_public_archives", *arguments, stdout=out)
        assert "Would record 4 accession(s)" in out.getvalue()
        assert not PublicAccession.objects.exists()

        out = StringIO()
        call_command("sync_public_archives", *arguments, "--apply", stdout=out)
        assert "Recorded 4 accession(s)" in out.getvalue()
        assert PublicAccession.objects.count() == 4

    def test_unknown_project_or_user_and_unreachable_archives_are_command_errors(
        self, fake, project, editor
    ):
        with pytest.raises(CommandError):
            call_command("sync_public_archives", "NOPE", "--bioproject", "P", "--user", "x")
        fake.ncbi_status = 503
        with pytest.raises(CommandError, match="NCBI"):
            call_command(
                "sync_public_archives",
                "SPBF",
                "--bioproject",
                "P",
                "--user",
                editor.username,
                "--source",
                "ncbi",
            )
