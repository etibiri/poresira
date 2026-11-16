# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.deposits import services, tasks
from poresira.deposits.archive import ArchiveError
from poresira.deposits.models import (
    DepositStatus,
    Environment,
    ItemKind,
    ItemStatus,
)
from poresira.deposits.tests.fake_archive import PASSWORD, USER, FakeArchive
from poresira.deposits.tests.test_deposit_data import reads_file, sequenced

pytestmark = pytest.mark.django_db


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
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


@pytest.fixture
def lead(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.LEAD).user


@pytest.fixture
def draft(editor, project):
    run, _members = sequenced(project, barcodes=(1, 2))
    for number in (1, 2):
        reads_file(project, run, f"pass/barcode0{number}/a.fastq.gz")
    return services.prepare_deposit(user=editor, run=run, title="Sweetpotato viromes")


def everything_the_database_holds(deposit):
    """Every text of the deposit that is stored, to look for the password in."""
    texts = [deposit.receipt, deposit.notes, deposit.webin_user]
    for item in deposit.items.all():
        texts += [item.message, item.accession, item.secondary_accession]
    return "\n".join(texts)


class TestSubmitting:
    def test_study_samples_and_reads_are_registered_and_their_accessions_kept(
        self, editor, draft, fake
    ):
        deposit = services.submit_deposit(user=editor, deposit=draft)

        assert deposit.status == DepositStatus.ACCEPTED and deposit.finished_at is not None
        assert (deposit.submitted_by, deposit.webin_user) == (editor, USER)
        items = deposit.items.all()
        study = items.get(kind=ItemKind.STUDY)
        assert study.status == ItemStatus.ACCEPTED and study.accession.startswith("PRJEB")
        samples = items.filter(kind=ItemKind.SAMPLE)
        assert samples.count() == 2
        assert all(s.accession.startswith("SAMEA") for s in samples)
        reads = items.filter(kind=ItemKind.READS)
        assert all(
            r.status == ItemStatus.ACCEPTED
            and r.accession.startswith("ERR")
            and r.secondary_accession.startswith("ERX")
            for r in reads
        )
        assert len(fake.requests) == 1  # study and samples in one request

    def test_the_manifest_carries_the_accessions_just_received(self, editor, draft, fake, tmp_path):
        deposit = services.submit_deposit(user=editor, deposit=draft)

        study = deposit.items.get(kind=ItemKind.STUDY).accession
        manifest = (tmp_path / "last_manifest").read_text()
        assert f"STUDY\t{study}\n" in manifest
        assert "SAMPLE\tSAMEA" in manifest and "INSTRUMENT\tMinION" in manifest
        assert "\nFASTQ\t" in manifest

    def test_the_password_is_never_stored(self, editor, draft):
        deposit = services.submit_deposit(user=editor, deposit=draft)

        assert PASSWORD not in everything_the_database_holds(deposit)
        assert "<RECEIPT" in deposit.receipt

    def test_the_test_service_is_used_for_a_test_deposit(self, editor, draft, tmp_path):
        services.submit_deposit(user=editor, deposit=draft)

        assert "-test" in (tmp_path / "last_args").read_text().split("\n")

    def test_the_hold_date_travels_with_the_registration(self, editor, project, fake):
        run, _members = sequenced(project, barcodes=(1,))
        reads_file(project, run, "pass/barcode01/a.fastq.gz")
        held = services.prepare_deposit(
            user=editor, run=run, title="T", hold_until=date.today() + timedelta(days=365)
        )

        services.submit_deposit(user=editor, deposit=held)

        assert "HoldUntilDate" in fake.requests[0][1]


class TestRefusals:
    def test_a_deposit_that_is_not_ready_is_refused_and_stays_a_draft(self, editor, project, fake):
        run, _members = sequenced(project, barcodes=(1, 2))
        draft = services.prepare_deposit(user=editor, run=run, title="T")  # no reads files

        with pytest.raises(services.DepositServiceError, match="not ready"):
            services.submit_deposit(user=editor, deposit=draft)

        draft.refresh_from_db()
        assert draft.status == DepositStatus.DRAFT and not fake.requests

    def test_without_a_webin_account_nothing_is_marked_or_sent(self, editor, draft, fake, settings):
        settings.ENA = {**settings.ENA, "WEBIN_PASSWORD": ""}

        with pytest.raises(ArchiveError, match="not configured"):
            services.submit_deposit(user=editor, deposit=draft)

        draft.refresh_from_db()
        assert draft.status == DepositStatus.DRAFT and not fake.requests

    def test_a_deposit_is_submitted_once(self, editor, draft):
        services.submit_deposit(user=editor, deposit=draft)

        with pytest.raises(services.DepositServiceError, match="draft"):
            services.submit_deposit(user=editor, deposit=draft)

    def test_readers_and_strangers_cannot_submit(self, project, draft):
        viewer = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user

        with pytest.raises(PermissionDenied):
            services.submit_deposit(user=viewer, deposit=draft)


class TestWhenTheArchiveSaysNo:
    def test_a_rejected_registration_rejects_the_deposit_and_sends_no_reads(
        self, editor, draft, fake, tmp_path
    ):
        fake.behaviour = "reject"

        deposit = services.submit_deposit(user=editor, deposit=draft)

        assert deposit.status == DepositStatus.REJECTED and deposit.finished_at is not None
        items = deposit.items.all()
        assert not items.filter(status=ItemStatus.ACCEPTED).exists()
        assert "sample title is missing" in items.get(kind=ItemKind.STUDY).message
        assert not (tmp_path / "last_args").exists()

    def test_an_unreachable_archive_rejects_the_deposit_with_a_message(
        self, editor, draft, settings
    ):
        settings.ENA = {**settings.ENA, "TEST_URL": "http://127.0.0.1:9/submit"}

        deposit = services.submit_deposit(user=editor, deposit=draft)

        assert deposit.status == DepositStatus.REJECTED
        assert "could not be reached" in deposit.items.get(kind=ItemKind.STUDY).message

    def test_a_sample_without_an_accession_is_rejected_with_its_reads(self, editor, draft, fake):
        fake.behaviour = "no_accession"

        deposit = services.submit_deposit(user=editor, deposit=draft)

        assert deposit.status == DepositStatus.REJECTED
        samples = deposit.items.filter(kind=ItemKind.SAMPLE)
        assert all(s.status == ItemStatus.REJECTED for s in samples)
        reads = deposit.items.filter(kind=ItemKind.READS)
        assert all(r.status == ItemStatus.REJECTED and r.accession == "" for r in reads)
        assert deposit.items.get(kind=ItemKind.STUDY).status == ItemStatus.ACCEPTED

    def test_a_failing_upload_keeps_what_was_accepted(self, editor, draft, tmp_path):
        (tmp_path / "fail_cli").write_text("")

        deposit = services.submit_deposit(user=editor, deposit=draft)

        assert deposit.status == DepositStatus.REJECTED
        assert deposit.items.get(kind=ItemKind.STUDY).status == ItemStatus.ACCEPTED
        reads = deposit.items.filter(kind=ItemKind.READS)
        assert all("invalid FASTQ" in r.message and PASSWORD not in r.message for r in reads)
        assert PASSWORD not in everything_the_database_holds(deposit)

    def test_one_failing_reads_item_does_not_stop_the_others(
        self, editor, draft, tmp_path, monkeypatch
    ):
        reads = list(draft.items.filter(kind=ItemKind.READS).order_by("alias"))
        marker = tmp_path / "fail_cli"
        original = services.EnaArchive.submit_reads

        def fail_first(self, manifest, name, identifier):
            if name == reads[0].alias:
                marker.write_text("")
            elif marker.exists():
                marker.unlink()
            return original(self, manifest, name, identifier)

        monkeypatch.setattr(services.EnaArchive, "submit_reads", fail_first)
        deposit = services.submit_deposit(user=editor, deposit=draft)

        statuses = {i.alias: i.status for i in deposit.items.filter(kind=ItemKind.READS)}
        assert statuses[reads[0].alias] == ItemStatus.REJECTED
        assert statuses[reads[1].alias] == ItemStatus.ACCEPTED
        assert deposit.status == DepositStatus.REJECTED


class TestProduction:
    @pytest.fixture
    def accepted_test(self, editor, draft):
        return services.submit_deposit(user=editor, deposit=draft)

    def future(self):
        return date.today() + timedelta(days=365)

    def test_only_an_accepted_test_deposit_can_be_copied_by_someone_who_may_publish(
        self, lead, editor, draft, accepted_test
    ):
        with pytest.raises(PermissionDenied):
            services.copy_for_production(
                user=editor, source=accepted_test, hold_until=self.future(), permits_confirmed=True
            )
        fresh = services.prepare_deposit(
            user=editor, run=draft.items.filter(kind="reads").first().sequencing_run, title="T2"
        )
        with pytest.raises(services.DepositServiceError, match="accepted"):
            services.copy_for_production(
                user=lead, source=fresh, hold_until=self.future(), permits_confirmed=True
            )

    def test_the_permits_and_a_future_date_are_required(self, lead, accepted_test):
        with pytest.raises(ValidationError):
            services.copy_for_production(
                user=lead, source=accepted_test, hold_until=self.future(), permits_confirmed=False
            )
        with pytest.raises(ValidationError):
            services.copy_for_production(
                user=lead, source=accepted_test, hold_until=date.today(), permits_confirmed=True
            )

    def test_the_production_deposit_needs_the_project_code_and_a_lead(
        self, lead, editor, accepted_test, fake, tmp_path
    ):
        production = services.copy_for_production(
            user=lead, source=accepted_test, hold_until=self.future(), permits_confirmed=True
        )
        assert production.environment == Environment.PRODUCTION
        assert production.compliance_confirmed_by == lead
        requests_before = len(fake.requests)

        with pytest.raises(PermissionDenied):
            services.submit_deposit(user=editor, deposit=production, confirm="SPBF")
        with pytest.raises(services.DepositServiceError, match="code of the project"):
            services.submit_deposit(user=lead, deposit=production, confirm="spbf-nope")
        assert len(fake.requests) == requests_before

        done = services.submit_deposit(user=lead, deposit=production, confirm="SPBF")

        assert done.status == DepositStatus.ACCEPTED
        assert "-test" not in (tmp_path / "last_args").read_text().split("\n")
        assert "HoldUntilDate" in fake.requests[-1][1]

    def test_what_the_archive_accepted_in_production_is_not_registered_again(
        self, lead, editor, accepted_test, fake
    ):
        first = services.copy_for_production(
            user=lead, source=accepted_test, hold_until=self.future(), permits_confirmed=True
        )
        services.submit_deposit(user=lead, deposit=first, confirm="SPBF")
        study = first.items.get(kind=ItemKind.STUDY).accession
        requests_before = len(fake.requests)

        again = services.copy_for_production(
            user=lead, source=accepted_test, hold_until=self.future(), permits_confirmed=True
        )

        reused = again.items.get(kind=ItemKind.STUDY)
        assert (reused.status, reused.accession) == (ItemStatus.ACCEPTED, study)
        assert all(
            i.status == ItemStatus.ACCEPTED for i in again.items.filter(kind=ItemKind.SAMPLE)
        )
        assert "Already registered" in reused.message
        services.submit_deposit(user=lead, deposit=again, confirm="SPBF")
        assert len(fake.requests) == requests_before  # nothing to register; reads only

    def test_test_accessions_are_never_reused_for_production(self, lead, accepted_test):
        production = services.copy_for_production(
            user=lead, source=accepted_test, hold_until=self.future(), permits_confirmed=True
        )

        assert all(
            i.status == ItemStatus.PENDING
            for i in production.items.filter(kind__in=[ItemKind.STUDY, ItemKind.SAMPLE])
        )


class TestTask:
    def test_the_agent_submits_and_reports_the_status(self, editor, draft):
        assert tasks.submit_deposit_task(str(draft.pk), str(editor.pk)) == "accepted"

    def test_a_refusal_is_written_in_the_notes_and_the_deposit_stays_a_draft(
        self, editor, project, fake
    ):
        run, _members = sequenced(project, barcodes=(1,))
        draft = services.prepare_deposit(user=editor, run=run, title="T")

        assert tasks.submit_deposit_task(str(draft.pk), str(editor.pk)) == "refused"

        draft.refresh_from_db()
        assert draft.status == DepositStatus.DRAFT and "not ready" in draft.notes

    def test_the_task_uses_the_deposits_queue(self):
        from config import celery_app

        route = celery_app.amqp.router.route({}, tasks.submit_deposit_task.name)
        assert route["queue"].name == "deposits"
