# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import date

import pytest
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from poresira.core.audit import verify_audit_chain, with_reason
from poresira.core.identifiers import is_valid_identifier
from poresira.core.tests.factories import ProjectFactory, UserFactory
from poresira.core.voiding import HardDeleteForbiddenError
from poresira.deposits.models import (
    DEPOSIT_TYPE_CODE,
    Deposit,
    DepositStatus,
    Environment,
    ItemStatus,
)
from poresira.deposits.tests.factories import (
    DepositFactory,
    DepositFileFactory,
    ReadsItemFactory,
    SampleItemFactory,
    StudyItemFactory,
    sent_fields,
)
from poresira.samples.tests.factories import SampleFactory
from poresira.sequencing.tests.factories import SequencingRunFactory
from poresira.storage.models import FileKind
from poresira.storage.tests.factories import DataFileFactory

pytestmark = pytest.mark.django_db


def refuses(error, action):
    with pytest.raises(error), transaction.atomic():
        action()


def submitting(**attributes):
    return DepositFactory(status=DepositStatus.SUBMITTING, **sent_fields(), **attributes)


def production(**attributes):
    return DepositFactory(
        environment=Environment.PRODUCTION,
        hold_until=date(2028, 1, 1),
        compliance_confirmed_at=timezone.now(),
        **attributes,
    )


class TestDeposits:
    def test_a_deposit_gets_a_valid_identifier_and_starts_as_a_test_draft(self):
        deposit = DepositFactory()

        assert deposit.identifier.split("-")[1].startswith(DEPOSIT_TYPE_CODE)
        assert is_valid_identifier(deposit.identifier)
        assert (deposit.status, deposit.environment) == (DepositStatus.DRAFT, Environment.TEST)
        assert (deposit.archive, deposit.sample_checklist) == ("ena", "ERC000011")

    def test_the_production_service_needs_a_release_date_and_the_permits_checked(self):
        production()

        refuses(IntegrityError, lambda: DepositFactory(environment=Environment.PRODUCTION))
        refuses(
            IntegrityError,
            lambda: DepositFactory(environment=Environment.PRODUCTION, hold_until=date(2028, 1, 1)),
        )
        refuses(
            IntegrityError,
            lambda: DepositFactory(
                environment=Environment.PRODUCTION, compliance_confirmed_at=timezone.now()
            ),
        )

    def test_a_deposit_that_was_sent_has_a_date_and_an_author(self):
        refuses(IntegrityError, lambda: DepositFactory(status=DepositStatus.SUBMITTING))
        submitting()

    def test_a_finished_deposit_has_an_end_date(self):
        refuses(
            IntegrityError,
            lambda: DepositFactory(status=DepositStatus.ACCEPTED, **sent_fields()),
        )
        DepositFactory(status=DepositStatus.REJECTED, finished_at=timezone.now(), **sent_fields())

    def test_what_identifies_a_deposit_cannot_change(self):
        deposit = DepositFactory()

        deposit.project = ProjectFactory()
        refuses(DatabaseError, deposit.save)
        deposit.refresh_from_db()
        deposit.archive = "other"
        refuses(DatabaseError, deposit.save)

    def test_a_draft_can_change_freely_and_a_sent_deposit_keeps_what_was_sent(self):
        deposit = DepositFactory()
        deposit.title, deposit.hold_until = "New title", date(2027, 1, 1)
        deposit.save()

        sent = submitting()
        for name, value in (
            ("title", "Other"),
            ("environment", Environment.PRODUCTION),
            ("hold_until", date(2030, 1, 1)),
            ("sample_checklist", "ERC000020"),
            ("library_strategy", "AMPLICON"),
        ):
            setattr(sent, name, value)
            refuses(DatabaseError, sent.save)
            sent.refresh_from_db()

    def test_the_progress_of_a_sent_deposit_is_recorded_without_a_reason(self):
        sent = submitting()

        sent.receipt, sent.notes = "<RECEIPT success='true'/>", "Queued"
        sent.save()
        sent.status, sent.finished_at = DepositStatus.ACCEPTED, timezone.now()
        sent.save()

    def test_a_finished_deposit_stays_finished(self):
        done = DepositFactory(
            status=DepositStatus.ACCEPTED, finished_at=timezone.now(), **sent_fields()
        )

        done.status = DepositStatus.DRAFT
        refuses(DatabaseError, done.save)

    def test_a_deposit_is_voided_never_deleted(self):
        deposit = DepositFactory()

        with pytest.raises(HardDeleteForbiddenError):
            Deposit.objects.filter(pk=deposit.pk).delete()
        refuses(DatabaseError, lambda: Deposit.objects.filter(pk=deposit.pk)._raw_delete("default"))
        deposit.void(user=UserFactory(), reason="Started by mistake")


class TestItems:
    def test_each_kind_of_item_carries_what_it_needs(self):
        deposit = DepositFactory()
        study = StudyItemFactory(deposit=deposit)
        sample = SampleItemFactory(deposit=deposit)
        reads = ReadsItemFactory(deposit=deposit, sample_item=sample)

        assert (study.sample, sample.sequencing_run, reads.sample_item) == (None, None, sample)

    def test_links_must_match_the_kind(self):
        deposit = DepositFactory()
        sample = SampleFactory(project=deposit.project)

        refuses(IntegrityError, lambda: StudyItemFactory(deposit=deposit, sample=sample))
        refuses(
            IntegrityError,
            lambda: SampleItemFactory(
                deposit=deposit,
                sequencing_run=SequencingRunFactory(library__project=deposit.project),
            ),
        )
        refuses(
            IntegrityError,
            lambda: ReadsItemFactory(
                deposit=deposit, sample_item=None, sample=SampleFactory(project=deposit.project)
            ),
        )

    def test_items_are_added_to_a_draft_only(self):
        sent = submitting()

        refuses(DatabaseError, lambda: StudyItemFactory(deposit=sent))

    def test_samples_and_runs_belong_to_the_project_of_the_deposit(self):
        deposit = DepositFactory()

        refuses(DatabaseError, lambda: SampleItemFactory(deposit=deposit, sample=SampleFactory()))
        sample_item = SampleItemFactory(deposit=deposit)
        refuses(
            DatabaseError,
            lambda: ReadsItemFactory(
                deposit=deposit, sample_item=sample_item, sequencing_run=SequencingRunFactory()
            ),
        )

    def test_reads_belong_to_a_sample_of_the_same_deposit(self):
        deposit = DepositFactory()
        foreign_sample_item = SampleItemFactory()

        refuses(
            DatabaseError,
            lambda: ReadsItemFactory(deposit=deposit, sample_item=foreign_sample_item),
        )
        refuses(
            DatabaseError,
            lambda: ReadsItemFactory(
                deposit=deposit, sample_item=StudyItemFactory(deposit=deposit)
            ),
        )

    def test_an_alias_is_unique_in_a_deposit_and_free_again_once_voided(self):
        deposit = DepositFactory()
        first = StudyItemFactory(deposit=deposit, alias="study_a")

        refuses(IntegrityError, lambda: StudyItemFactory(deposit=deposit, alias="study_a"))
        StudyItemFactory(alias="study_a")  # another deposit
        first.void(user=UserFactory(), reason="Renamed")
        StudyItemFactory(deposit=deposit, alias="study_a")

    def test_what_an_item_is_cannot_change(self):
        item = SampleItemFactory()

        item.alias = "renamed"
        refuses(DatabaseError, item.save)
        item.refresh_from_db()
        item.sample = SampleFactory(project=item.deposit.project)
        refuses(DatabaseError, item.save)

    @pytest.mark.parametrize(
        ("factory", "accession", "valid"),
        [
            (StudyItemFactory, "PRJEB12345", True),
            (StudyItemFactory, "PRJNA1460897", True),
            (StudyItemFactory, "SAMEA123", False),
            (SampleItemFactory, "SAMEA1234567", True),
            (SampleItemFactory, "SAMN12345", True),
            (SampleItemFactory, "PRJEB1", False),
            (ReadsItemFactory, "ERR1234567", True),
            (ReadsItemFactory, "ERX1234567", False),
        ],
    )
    def test_accessions_follow_the_archive_patterns(self, factory, accession, valid):
        item = factory()
        start_submitting(item.deposit)

        if valid:
            give_accession(item, accession)
        else:
            refuses(IntegrityError, lambda: give_accession(item, accession))
        item.refresh_from_db()
        assert item.accession == (accession if valid else "")

    def test_accessions_are_recorded_while_submitting_and_never_change(self):
        item = StudyItemFactory()
        refuses(DatabaseError, lambda: give_accession(item, "PRJEB1"))  # still a draft

        item.deposit.status = DepositStatus.SUBMITTING
        for name, value in sent_fields().items():
            setattr(item.deposit, name, value)
        item.deposit.save()
        give_accession(item, "PRJEB1")
        refuses(DatabaseError, lambda: give_accession(item, "PRJEB2"))
        give_accession(item, "PRJEB1")  # the same value again is no change

    def test_an_experiment_accession_belongs_to_reads(self):
        reads = ReadsItemFactory()
        start_submitting(reads.deposit)

        give_accession(reads, "ERR1", secondary="ERX1")
        study = StudyItemFactory(deposit=DepositFactory())
        start_submitting(study.deposit)
        refuses(IntegrityError, lambda: give_accession(study, "PRJEB1", secondary="ERX1"))

    def test_an_accepted_item_has_an_accession(self):
        item = StudyItemFactory()
        start_submitting(item.deposit)

        item.status = ItemStatus.ACCEPTED
        refuses(IntegrityError, item.save)
        give_accession(item, "PRJEB9", status=ItemStatus.ACCEPTED)


class TestFiles:
    def test_a_reads_file_is_a_fastq_or_bam_of_the_project_added_to_a_draft(self):
        row = DepositFileFactory()
        project = row.item.deposit.project

        DepositFileFactory(
            item=row.item,
            file=DataFileFactory(
                project=project, kind=FileKind.BAM, sequencing_run=row.item.sequencing_run
            ),
        )
        refuses(
            DatabaseError,
            lambda: DepositFileFactory(
                item=row.item,
                file=DataFileFactory(
                    project=project, kind=FileKind.POD5, sequencing_run=row.item.sequencing_run
                ),
            ),
        )
        refuses(
            DatabaseError,
            lambda: DepositFileFactory(item=row.item, file=DataFileFactory(kind=FileKind.FASTQ)),
        )

    def test_only_a_reads_item_takes_files(self):
        sample_item = SampleItemFactory()

        refuses(
            DatabaseError,
            lambda: DepositFileFactory(
                item=sample_item,
                file=DataFileFactory(
                    project=sample_item.deposit.project,
                    kind=FileKind.FASTQ,
                    sequencing_run=SequencingRunFactory(
                        library__project=sample_item.deposit.project
                    ),
                ),
            ),
        )

    def test_files_are_added_while_the_deposit_is_a_draft(self):
        reads = ReadsItemFactory()
        start_submitting(reads.deposit)

        refuses(DatabaseError, lambda: DepositFileFactory(item=reads))

    def test_a_file_is_listed_once_per_item_with_a_well_formed_checksum(self):
        row = DepositFileFactory()

        refuses(IntegrityError, lambda: DepositFileFactory(item=row.item, file=row.file))
        refuses(IntegrityError, lambda: DepositFileFactory(item=row.item, sha256="xyz"))

    def test_a_listed_file_cannot_change(self):
        row = DepositFileFactory()

        row.sha256 = "b" * 64
        refuses(DatabaseError, row.save)


def give_accession(item, accession, secondary="", status=None):
    item.refresh_from_db()
    item.accession = accession
    item.secondary_accession = secondary
    if status:
        item.status = status
    item.save()


def start_submitting(deposit):
    deposit.status = DepositStatus.SUBMITTING
    for name, value in sent_fields().items():
        setattr(deposit, name, value)
    deposit.save()


def test_changes_are_audited_and_the_chain_is_intact():
    deposit = DepositFactory()
    item = StudyItemFactory(deposit=deposit)
    DepositFileFactory()
    with with_reason("Reworded"):
        deposit.title = "Reworded"
        deposit.save()
    start_submitting(deposit)
    give_accession(item, "PRJEB77")

    assert verify_audit_chain().is_intact
    assert deposit.events.filter(pgh_label="insert").count() == 1
    assert item.events.filter(pgh_label="update").count() == 1
