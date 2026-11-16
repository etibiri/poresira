# SPDX-License-Identifier: AGPL-3.0-or-later
import io
import json
import zipfile
from datetime import date, timedelta

import pytest
from django.urls import reverse

from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.deposits import services
from poresira.deposits.models import Deposit, DepositStatus, Environment, ItemKind, ItemStatus
from poresira.deposits.tests.factories import DepositFactory, PublicAccessionFactory
from poresira.deposits.tests.fake_archive import PASSWORD, USER, FakeArchive
from poresira.deposits.tests.test_deposit_data import reads_file, sequenced

pytestmark = pytest.mark.django_db


def url(name, *args):
    return reverse(f"deposits:{name}", args=args)


@pytest.fixture
def fake(tmp_path):
    archive = FakeArchive(tmp_path)
    yield archive
    archive.stop()


@pytest.fixture(autouse=True)
def ena(settings, fake, tmp_path):
    settings.ENA = {
        "WEBIN_USER": USER,
        "WEBIN_PASSWORD": PASSWORD,
        "TEST_URL": fake.url,
        "PRODUCTION_URL": fake.url,
        "WEBIN_CLI": fake.cli,
        "WORK_DIR": str(tmp_path / "work"),
        "TIMEOUT_SECONDS": 10,
        "CLI_TIMEOUT_SECONDS": 30,
    }


@pytest.fixture
def project():
    return ProjectFactory(code="SPBF")


@pytest.fixture
def editor(project, client):
    user = ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user
    client.force_login(user)
    return user


@pytest.fixture
def run(project):
    run, _members = sequenced(project, barcodes=(1, 2))
    for number in (1, 2):
        reads_file(project, run, f"pass/barcode0{number}/a.fastq.gz")
    return run


@pytest.fixture
def draft(editor, run):
    return services.prepare_deposit(user=editor, run=run, title="Sweetpotato viromes")


class TestAccess:
    def test_anonymous_users_are_sent_to_login(self, client, draft):
        client.logout()

        assert client.get(url("deposit-list")).status_code == 302
        for name in (
            "deposit-detail",
            "deposit-submit",
            "deposit-production",
            "deposit-void",
            "deposit-ro-crate",
            "deposit-files",
        ):
            assert client.get(url(name, draft.pk)).status_code == 302

    def test_a_deposit_of_a_foreign_project_is_not_found(self, client, editor):
        assert client.get(url("deposit-detail", DepositFactory().pk)).status_code == 404

    def test_read_only_members_can_see_and_download_but_not_change(self, client, project, draft):
        client.force_login(ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user)

        assert client.get(url("deposit-detail", draft.pk)).status_code == 200
        assert client.get(url("deposit-ro-crate", draft.pk)).status_code == 200
        assert client.get(url("deposit-files", draft.pk)).status_code == 200
        for name in ("deposit-submit", "deposit-void", "deposit-production"):
            assert client.get(url(name, draft.pk)).status_code == 403
        assert client.get(
            url("deposit-new", draft.items.first().sequencing_run_id)
        ).status_code in (
            403,
            404,
        )


class TestPages:
    def test_the_list_and_the_detail_show_the_deposit_and_its_report(self, client, editor, draft):
        assert draft.identifier in client.get(url("deposit-list")).content.decode()

        page = client.get(url("deposit-detail", draft.pk)).content.decode()

        assert "Ready to submit." in page and "Test deposit" in page
        assert "Tie_BFA1" in page and "Tie_BFA2" in page

    def test_the_list_filters(self, client, editor, draft, project):
        other = DepositFactory(project=project, title="Other")

        page = client.get(url("deposit-list"), {"status": "draft", "environment": "test"})

        assert (
            draft.identifier in page.content.decode() and other.identifier in page.content.decode()
        )
        none = client.get(url("deposit-list"), {"environment": "production"}).content.decode()
        assert draft.identifier not in none

    def test_what_is_wrong_is_listed_with_a_way_to_leave_the_object_out(
        self, client, editor, project
    ):
        run, _members = sequenced(project, barcodes=(1,))  # no reads file
        deposit = services.prepare_deposit(user=editor, run=run, title="T")

        page = client.get(url("deposit-detail", deposit.pk)).content.decode()

        assert "Not ready to submit" in page and "has no reads file" in page
        item = deposit.items.get(kind=ItemKind.READS)
        assert url("item-leave-out", item.pk) in page

    def test_the_run_page_offers_a_deposit_and_lists_the_ones_it_has(
        self, client, editor, run, draft
    ):
        page = client.get(reverse("sequencing:run-detail", args=[run.pk])).content.decode()

        assert url("deposit-new", run.pk) in page and draft.identifier in page

    def test_the_sample_page_shows_what_the_public_archives_hold(
        self, client, editor, project, draft
    ):
        sample = draft.items.filter(kind=ItemKind.SAMPLE).first().sample
        PublicAccessionFactory(project=project, accession="SAMN57660728", sample=sample)
        PublicAccessionFactory(
            project=project, kind="sra_run", accession="SRR38354347", sample=sample
        )

        page = client.get(reverse("samples:sample-detail", args=[sample.pk])).content.decode()

        assert "SAMN57660728" in page and "SRR38354347" in page and "Public archives" in page

    def test_a_sample_nothing_is_known_about_has_no_such_section(self, client, editor, draft):
        sample = draft.items.filter(kind=ItemKind.SAMPLE).first().sample

        page = client.get(reverse("samples:sample-detail", args=[sample.pk])).content.decode()

        assert "Public archives" not in page


class TestPreparing:
    def test_a_deposit_is_prepared_from_the_run_page(self, client, editor, run):
        page = client.get(url("deposit-new", run.pk)).content.decode()
        assert 'name="title"' in page and 'name="library_strategy"' in page

        response = client.post(
            url("deposit-new", run.pk),
            {
                "title": "RCA reads",
                "description": "Sweetpotato",
                "sample_checklist": "ERC000011",
                "library_source": "METAGENOMIC",
                "library_selection": "RANDOM",
                "library_strategy": "WGA",
            },
        )

        deposit = Deposit.objects.get()
        assert response.url == url("deposit-detail", deposit.pk)
        assert (deposit.library_source, deposit.library_strategy) == ("METAGENOMIC", "WGA")
        assert deposit.environment == Environment.TEST and deposit.items.count() == 5

    def test_a_title_is_required(self, client, editor, run):
        response = client.post(
            url("deposit-new", run.pk),
            {"title": "", "sample_checklist": "ERC000011", "library_source": "GENOMIC",
             "library_selection": "RANDOM", "library_strategy": "WGS"},
        )  # fmt: skip

        assert response.status_code == 200 and not Deposit.objects.exists()


class TestSubmitting:
    @pytest.fixture
    def queued(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "poresira.deposits.views.submit_deposit_task.delay", lambda *args: calls.append(args)
        )
        return calls

    def test_a_test_deposit_is_handed_to_the_agent(self, client, editor, draft, queued):
        response = client.post(url("deposit-submit", draft.pk), {})

        assert response.status_code == 302
        assert queued == [(str(draft.pk), str(editor.pk), "")]

    def test_a_deposit_that_is_not_ready_is_refused_on_the_spot(
        self, client, editor, project, queued
    ):
        run, _members = sequenced(project, barcodes=(1,))
        deposit = services.prepare_deposit(user=editor, run=run, title="T")

        response = client.post(url("deposit-submit", deposit.pk), {})

        assert response.status_code == 200 and b"not ready" in response.content
        assert queued == []

    def test_only_a_draft_can_be_submitted(self, client, editor, draft):
        done = services.submit_deposit(user=editor, deposit=draft)

        assert client.get(url("deposit-submit", done.pk)).status_code == 403


class TestProduction:
    @pytest.fixture
    def accepted(self, editor, draft):
        return services.submit_deposit(user=editor, deposit=draft)

    @pytest.fixture
    def lead(self, project):
        return ProjectMembershipFactory(project=project, role=ProjectRole.LEAD).user

    def future(self):
        return (date.today() + timedelta(days=365)).isoformat()

    def test_only_someone_who_may_publish_sees_and_uses_the_real_deposit(
        self, client, editor, accepted
    ):
        assert (
            b"Prepare the real deposit"
            not in client.get(url("deposit-detail", accepted.pk)).content
        )
        assert client.get(url("deposit-production", accepted.pk)).status_code == 403

    def test_a_lead_prepares_the_real_deposit_with_the_permits_and_a_date(
        self, client, lead, accepted
    ):
        client.force_login(lead)
        page = client.get(url("deposit-detail", accepted.pk)).content.decode()
        assert "Prepare the real deposit" in page

        refused = client.post(url("deposit-production", accepted.pk), {"hold_until": self.future()})
        assert (
            refused.status_code == 200
            and Deposit.objects.filter(environment="production").count() == 0
        )

        response = client.post(
            url("deposit-production", accepted.pk),
            {"hold_until": self.future(), "permits_confirmed": "on"},
        )

        real = Deposit.objects.get(environment=Environment.PRODUCTION)
        assert response.url == url("deposit-detail", real.pk)
        assert real.compliance_confirmed_by == lead and real.status == DepositStatus.DRAFT

    def test_the_real_submission_needs_the_project_code(self, client, lead, accepted, monkeypatch):
        client.force_login(lead)
        calls = []
        monkeypatch.setattr(
            "poresira.deposits.views.submit_deposit_task.delay", lambda *args: calls.append(args)
        )
        real = services.copy_for_production(
            user=lead,
            source=accepted,
            hold_until=date.today() + timedelta(days=365),
            permits_confirmed=True,
        )
        page = client.get(url("deposit-submit", real.pk)).content.decode()
        assert "Submit for real" in page and "Type SPBF" in page

        wrong = client.post(url("deposit-submit", real.pk), {"confirm": "nope"})
        assert wrong.status_code == 200 and calls == []

        client.post(url("deposit-submit", real.pk), {"confirm": "SPBF"})

        assert calls == [(str(real.pk), str(lead.pk), "SPBF")]

    def test_a_contributor_cannot_submit_the_real_deposit(self, client, lead, accepted, project):
        real = services.copy_for_production(
            user=lead,
            source=accepted,
            hold_until=date.today() + timedelta(days=365),
            permits_confirmed=True,
        )
        client.force_login(
            ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user
        )

        assert client.get(url("deposit-submit", real.pk)).status_code == 403


class TestLeavingOutAndVoiding:
    def test_reads_are_left_out_with_a_reason_and_leave_the_report(self, client, editor, project):
        run, _members = sequenced(project, barcodes=(1, 2))
        reads_file(project, run, "pass/barcode01/a.fastq.gz")  # barcode 2 has none
        deposit = services.prepare_deposit(user=editor, run=run, title="T")
        empty = next(i for i in deposit.items.filter(kind="reads") if not i.files.exists())

        client.post(url("item-leave-out", empty.pk), {"reason": "Already in SRA"})
        empty.refresh_from_db()
        assert empty.is_voided

        page = client.get(url("deposit-detail", deposit.pk)).content.decode()
        assert "Ready to submit." in page

    def test_the_study_and_the_samples_with_reads_cannot_be_left_out(self, client, editor, draft):
        study = draft.items.get(kind=ItemKind.STUDY)
        sample = draft.items.filter(kind=ItemKind.SAMPLE).first()

        for item in (study, sample):
            response = client.post(url("item-leave-out", item.pk), {"reason": "No"})
            assert response.status_code == 200
            item.refresh_from_db()
            assert not item.is_voided

    def test_a_finished_deposit_has_nothing_to_leave_out(self, client, editor, draft):
        done = services.submit_deposit(user=editor, deposit=draft)
        item = done.items.filter(kind=ItemKind.READS).first()

        assert client.get(url("item-leave-out", item.pk)).status_code == 403

    def test_a_deposit_is_voided_with_a_reason(self, client, editor, draft):
        client.post(url("deposit-void", draft.pk), {"reason": "Started by mistake"})

        draft.refresh_from_db()
        assert draft.is_voided and draft.void_reason == "Started by mistake"


class TestDownloads:
    def test_the_files_to_be_sent_are_downloaded_to_be_read_first(self, client, editor, draft):
        response = client.get(url("deposit-files", draft.pk))

        assert response["Content-Disposition"] == (
            f'attachment; filename="{draft.identifier}-submission-files.zip"'
        )
        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        assert {"submission.xml", "study.xml", "samples.xml"} <= set(names)
        assert sum(name.startswith("manifests/") for name in names) == 2

    def test_the_manifests_show_placeholders_until_the_archive_answers(self, client, editor, draft):
        response = client.get(url("deposit-files", draft.pk))

        archive = zipfile.ZipFile(io.BytesIO(response.content))
        manifest = archive.read(next(n for n in archive.namelist() if n.startswith("manifests/")))
        assert b"<STUDY ACCESSION>" in manifest and b"<SAMPLE ACCESSION>" in manifest

    def test_the_ro_crate_is_downloaded(self, client, editor, draft):
        response = client.get(url("deposit-ro-crate", draft.pk))

        assert response["Content-Disposition"].endswith('-ro-crate.zip"')
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        assert json.loads(archive.read("ro-crate-metadata.json"))["@graph"][1]["@id"] == "./"

    def test_the_files_of_an_accepted_deposit_leave_out_what_is_registered(
        self, client, editor, draft
    ):
        done = services.submit_deposit(user=editor, deposit=draft)

        response = client.get(url("deposit-files", done.pk))

        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        assert "study.xml" not in names and "samples.xml" not in names
        assert done.items.filter(status=ItemStatus.ACCEPTED).exists()
