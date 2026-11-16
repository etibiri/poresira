# SPDX-License-Identifier: AGPL-3.0-or-later
import io
import zipfile
from decimal import Decimal

import pytest
from django.urls import reverse

from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.deposits import ncbi, services
from poresira.deposits.collect import preflight
from poresira.deposits.models import Archive, DepositStatus, ItemKind, ItemStatus
from poresira.deposits.tests.factories import PublicAccessionFactory
from poresira.deposits.tests.test_deposit_data import reads_file, sequenced

pytestmark = pytest.mark.django_db


def sample(**attributes):
    defaults = {
        "name": "dep_S1",
        "title": "Tie_BFA316",
        "organism": "plant metagenome",
        "isolate": "Tie_BFA316",
        "host": "Ipomoea batatas",
        "collection_date": "2016-04-16",
        "geo_loc_name": "Burkina Faso: Nahouri, Tiebele",
        "lat_lon": "11.08 N 0.90 W",
    }
    return ncbi.NcbiSample(**{**defaults, **attributes})


def reads(**attributes):
    defaults = {
        "library_id": "dep_reads_1",
        "title": "T: Tie_BFA316",
        "sample_name": "dep_S1",
        "biosample_accession": "",
        "instrument_model": "MinION",
        "library_strategy": "WGA",
        "library_source": "METAGENOMIC",
        "library_selection": "RANDOM",
        "design_description": "SQK-NBD114.96 library of amplification product",
        "filetype": "fastq",
        "file_names": ("a.fastq.gz",),
    }
    return ncbi.NcbiReads(**{**defaults, **attributes})


class TestFormats:
    @pytest.mark.parametrize(
        ("latitude", "longitude", "text"),
        [
            ("11.08", "-0.90", "11.08 N 0.90 W"),
            ("10.94000", "-5.11000", "10.94 N 5.11 W"),
            ("-3.5", "30", "3.50 S 30.00 E"),
            ("0", "0", "0.00 N 0.00 E"),
            ("11.123456", "-0.5", "11.123456 N 0.50 W"),
        ],
    )
    def test_coordinates_are_degrees_and_a_hemisphere(self, latitude, longitude, text):
        assert ncbi.lat_lon(Decimal(latitude), Decimal(longitude)) == text

    def test_the_place_is_the_country_a_colon_and_the_places(self):
        assert ncbi.geo_loc_name("Burkina Faso", "Nahouri", "Tiebele") == (
            "Burkina Faso: Nahouri, Tiebele"
        )
        assert ncbi.geo_loc_name("Burkina Faso", "", "") == "Burkina Faso"
        assert ncbi.geo_loc_name("Burkina Faso", "", "Tiebele") == "Burkina Faso: Tiebele"


class TestTables:
    def test_the_biosample_table_has_the_package_columns_and_one_row_per_sample(self):
        lines = ncbi.biosample_table([sample(), sample(name="dep_S2", isolate="x")]).splitlines()

        header = lines[0].split("\t")
        assert tuple(header) == ncbi.BIOSAMPLE_COLUMNS and len(lines) == 3
        row = dict(zip(header, lines[1].split("\t"), strict=True))
        assert row["organism"] == "plant metagenome"
        assert row["lat_lon"] == "11.08 N 0.90 W"
        assert (row["env_broad_scale"], row["env_medium"]) == ("not applicable",) * 2

    def test_a_value_cannot_shift_the_columns(self):
        table = ncbi.biosample_table([sample(title="a\tb\nc")])

        assert all(
            len(line.split("\t")) == len(ncbi.BIOSAMPLE_COLUMNS) for line in table.splitlines()
        )

    def test_the_sra_table_has_as_many_filename_columns_as_the_widest_experiment(self):
        table = ncbi.sra_table(
            [reads(), reads(library_id="r2", file_names=("a.fastq.gz", "b.fastq.gz", "c.fastq.gz"))]
        )

        lines = [line.split("\t") for line in table.splitlines()]
        assert lines[0][-3:] == ["filename", "filename2", "filename3"]
        assert lines[1][-3:] == ["a.fastq.gz", "", ""] and lines[2][-1] == "c.fastq.gz"
        row = dict(zip(lines[0], lines[1], strict=False))
        assert (row["platform"], row["instrument_model"], row["library_layout"]) == (
            "OXFORD_NANOPORE",
            "MinION",
            "single",
        )

    def test_an_existing_biosample_is_cited_by_its_accession(self):
        table = ncbi.sra_table([reads(sample_name="", biosample_accession="SAMN57660728")])

        row = dict(zip(*[line.split("\t") for line in table.splitlines()], strict=False))
        assert row["biosample_accession"] == "SAMN57660728" and row["sample_name"] == ""

    def test_the_upload_list_is_one_path_per_line(self):
        assert (
            ncbi.upload_list(["/d/a.fastq.gz", "/d/b.fastq.gz"]) == "/d/a.fastq.gz\n/d/b.fastq.gz\n"
        )

    def test_the_instructions_name_the_steps_and_the_caveat(self):
        text = ncbi.instructions("WBF-X26-00001-1", has_samples=True)

        assert "WBF-X26-00001-1" in text and "MIMS.me.plant-associated.6.0" in text
        assert "import_public_accessions" in text and "not checked against the template" in text
        assert "biosample_attributes.tsv" not in ncbi.instructions("X", has_samples=False)


@pytest.fixture
def project():
    return ProjectFactory(code="SPBF")


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


def prepared(editor, project, files=True, **sequencing):
    run, _members = sequenced(project, barcodes=(1, 2), **sequencing)
    if files:
        for number in (1, 2):
            reads_file(project, run, f"pass/barcode0{number}/reads_{number}.fastq.gz")
    return services.prepare_deposit(user=editor, run=run, title="T", archive=Archive.NCBI)


class TestNcbiDeposit:
    def test_an_ncbi_deposit_is_prepared_like_any_other_and_is_ready(self, editor, project):
        deposit = prepared(editor, project)

        assert deposit.archive == Archive.NCBI and not deposit.sends_itself and deposit.is_real
        assert preflight(deposit).ready

    def test_it_is_never_sent_by_the_agent(self, editor, project):
        deposit = prepared(editor, project)

        with pytest.raises(services.DepositServiceError, match="Submission Portal"):
            services.submit_deposit(user=editor, deposit=deposit)
        deposit.refresh_from_db()
        assert deposit.status == DepositStatus.DRAFT

    def test_the_files_for_the_portal_are_prepared(self, editor, project):
        deposit = prepared(editor, project)

        files = services.submission_files(deposit)

        assert set(files) == {
            "biosample_attributes.tsv",
            "sra_metadata.tsv",
            "files_to_upload.txt",
            "README.txt",
        }
        assert files["biosample_attributes.tsv"].count("\n") == 3
        assert files["sra_metadata.tsv"].count("\n") == 3
        assert files["files_to_upload.txt"].count("reads_") == 2
        header, first, *_rest = (
            line.split("\t") for line in files["biosample_attributes.tsv"].splitlines()
        )
        row = dict(zip(header, first, strict=True))
        assert row["geo_loc_name"].startswith("Burkina Faso: ")
        assert row["lat_lon"] == "11.08 N 0.90 W" and row["collection_date"] == "2016-04-16"

    def test_a_biosample_that_exists_is_cited_and_not_listed_for_creation(self, editor, project):
        deposit = prepared(editor, project)
        known = deposit.items.filter(kind=ItemKind.SAMPLE).first().sample
        PublicAccessionFactory(project=project, accession="SAMN57660728", sample=known)
        again = services.prepare_deposit(
            user=editor,
            run=deposit.items.filter(kind="reads").first().sequencing_run,
            title="T2",
            archive=Archive.NCBI,
        )

        cited = again.items.get(kind=ItemKind.SAMPLE, sample=known)
        assert (cited.status, cited.accession) == (ItemStatus.ACCEPTED, "SAMN57660728")
        files = services.submission_files(again)
        assert files["biosample_attributes.tsv"].count("\n") == 2  # header and the other sample
        assert "SAMN57660728" in files["sra_metadata.tsv"]

    def test_reads_already_in_sra_are_an_error_even_without_a_production_step(
        self, editor, project
    ):
        deposit = prepared(editor, project)
        known = deposit.items.filter(kind=ItemKind.SAMPLE).first().sample
        PublicAccessionFactory(project=project, kind="sra_run", accession="SRR1", sample=known)

        report = preflight(deposit)

        assert not report.ready
        assert any("already in SRA" in f.message for f in report.errors)

    def test_coordinates_are_required_by_ncbi_and_not_by_ena(self, editor, project):
        run, _members = sequenced(
            project, barcodes=(1,), event_attributes={"latitude": None, "longitude": None}
        )
        reads_file(project, run, "pass/barcode01/a.fastq.gz")
        ena = services.prepare_deposit(user=editor, run=run, title="T")
        portal = services.prepare_deposit(user=editor, run=run, title="T", archive=Archive.NCBI)

        assert preflight(ena).ready
        assert any("no coordinates" in f.message for f in preflight(portal).errors)

    def test_file_names_must_be_unique_in_a_submission(self, editor, project):
        run, _members = sequenced(project, barcodes=(1, 2))
        for number in (1, 2):
            reads_file(project, run, f"pass/barcode0{number}/reads.fastq.gz")  # the same name
        deposit = services.prepare_deposit(user=editor, run=run, title="T", archive=Archive.NCBI)

        errors = [f.message for f in preflight(deposit).errors]

        assert any("names must be unique" in m for m in errors)

    def test_the_collector_is_not_in_the_files(self, editor, project):
        deposit = prepared(editor, project)
        event = deposit.items.filter(kind=ItemKind.SAMPLE).first().sample.collection_event
        event.collector = "A Farmer"
        event.save()

        assert "A Farmer" not in "".join(services.submission_files(deposit).values())


class TestInterface:
    def test_the_form_offers_the_archive_and_the_page_has_no_send_button(
        self, client, editor, project
    ):
        client.force_login(editor)
        run, _members = sequenced(project, barcodes=(1, 2))
        for number in (1, 2):
            reads_file(project, run, f"pass/barcode0{number}/r_{number}.fastq.gz")
        page = client.get(reverse("deposits:deposit-new", args=[run.pk])).content.decode()
        assert 'name="archive"' in page

        response = client.post(
            reverse("deposits:deposit-new", args=[run.pk]),
            {
                "archive": "ncbi",
                "title": "T",
                "sample_checklist": "ERC000011",
                "library_source": "METAGENOMIC",
                "library_selection": "RANDOM",
                "library_strategy": "WGA",
            },
        )
        detail = client.get(response.url).content.decode()

        assert "does not send it" in detail and "Files for the Submission Portal" in detail
        assert "Submit to the test service" not in detail and "Submit for real" not in detail

    def test_the_download_holds_the_portal_files(self, client, editor, project):
        client.force_login(editor)
        deposit = prepared(editor, project)

        response = client.get(reverse("deposits:deposit-files", args=[deposit.pk]))

        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        assert "sra_metadata.tsv" in names and "README.txt" in names

    def test_the_deposit_wears_an_ncbi_badge(self, client, editor, project):
        client.force_login(editor)
        deposit = prepared(editor, project)

        assert (
            "NCBI</span>"
            in client.get(reverse("deposits:deposit-detail", args=[deposit.pk])).content.decode()
        )
