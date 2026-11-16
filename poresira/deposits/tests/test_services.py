# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.deposits import services
from poresira.deposits.models import DepositStatus, Environment, ItemKind
from poresira.deposits.tests.factories import sent_fields
from poresira.deposits.tests.test_deposit_data import reads_file, sequenced
from poresira.storage.models import FileKind

pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return ProjectFactory()


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


class TestBarcodeInPath:
    @pytest.mark.parametrize(
        ("path", "number"),
        [
            ("fastq_pass/barcode07/a.fastq.gz", 7),
            ("x/BC07_reads.fastq", 7),
            ("x/NB12.bam", 12),
            ("run_barcode101/a.fastq", 101),
            ("fastq_pass/unclassified/a.fastq", None),
            ("abc07/a.fastq", None),
        ],
    )
    def test_the_barcode_of_a_reads_file_is_read_from_its_path(self, path, number):
        assert services._barcode_of(path) == number


class TestPreparing:
    def test_a_draft_holds_the_study_the_samples_and_the_reads_with_their_files(
        self, editor, project
    ):
        run, members = sequenced(project, barcodes=(1, 2))
        first = reads_file(project, run, "pass/barcode01/a.fastq.gz")
        second = reads_file(project, run, "pass/barcode01/b.fastq.gz")
        other = reads_file(project, run, "pass/barcode02/a.fastq.gz")

        deposit = services.prepare_deposit(user=editor, run=run, title="Sweetpotato viromes")

        assert (deposit.status, deposit.environment) == (DepositStatus.DRAFT, Environment.TEST)
        items = deposit.items.all()
        assert items.filter(kind=ItemKind.STUDY).count() == 1
        assert items.filter(kind=ItemKind.SAMPLE).count() == 2
        reads = {item.sample: item for item in items.filter(kind=ItemKind.READS)}
        one, two = reads[members[0].sample], reads[members[1].sample]
        assert {row.file for row in one.files.all()} == {first, second}
        assert {row.file for row in two.files.all()} == {other}
        assert all(row.sha256 == row.file.sha256 for row in one.files.all())
        assert one.sample_item.sample == members[0].sample.parent
        assert one.alias.startswith(deposit.identifier) and run.identifier in one.alias

    def test_two_members_of_one_field_sample_share_its_biosample(self, editor, project):
        run, _members = sequenced(project, barcodes=(1, 2), shared_parent=True)

        deposit = services.prepare_deposit(user=editor, run=run, title="T")

        assert deposit.items.filter(kind=ItemKind.SAMPLE).count() == 1
        assert deposit.items.filter(kind=ItemKind.READS).count() == 2

    def test_fastq_is_preferred_and_bam_is_the_fallback(self, editor, project):
        run, _members = sequenced(project, barcodes=(1, 2))
        fastq = reads_file(project, run, "p/barcode01/a.fastq.gz")
        reads_file(project, run, "p/barcode01/a.bam", kind=FileKind.BAM)
        bam = reads_file(project, run, "p/barcode02/a.bam", kind=FileKind.BAM)
        reads_file(project, run, "p/barcode01/signal.pod5", kind=FileKind.POD5)

        deposit = services.prepare_deposit(user=editor, run=run, title="T")

        files = {
            row.file
            for row in deposit.items.get(sample__label__endswith="1", kind="reads").files.all()
        }
        assert files == {fastq}
        second = deposit.items.get(kind="reads", sample__label__endswith="2")
        assert {row.file for row in second.files.all()} == {bam}

    def test_a_run_that_was_not_barcoded_gives_all_its_files_to_its_only_sample(
        self, editor, project
    ):
        run, _members = sequenced(project, barcodes=(None,))
        calls = reads_file(project, run, "pass/calls.fastq.gz")

        deposit = services.prepare_deposit(user=editor, run=run, title="T")

        assert [row.file for row in deposit.items.get(kind="reads").files.all()] == [calls]

    def test_a_member_without_reads_is_kept_so_the_report_can_name_it(self, editor, project):
        run, _members = sequenced(project, barcodes=(1, 2))
        reads_file(project, run, "p/barcode01/a.fastq.gz")

        deposit = services.prepare_deposit(user=editor, run=run, title="T")

        empty = [item for item in deposit.items.filter(kind="reads") if not item.files.exists()]
        assert len(empty) == 1

    def test_production_is_not_prepared_directly(self, editor, project):
        run, _members = sequenced(project)

        with pytest.raises(ValidationError, match="test service"):
            services.prepare_deposit(
                user=editor, run=run, title="T", environment=Environment.PRODUCTION
            )

    def test_unknown_options_and_empty_libraries_are_refused(self, editor, project):
        run, _members = sequenced(project)

        with pytest.raises(TypeError):
            services.prepare_deposit(user=editor, run=run, title="T", archive="other")
        deposit = services.prepare_deposit(
            user=editor, run=run, title="T", library_strategy="AMPLICON"
        )
        assert deposit.library_strategy == "AMPLICON"

    def test_readers_and_strangers_cannot_prepare(self, project):
        run, _members = sequenced(project)
        viewer = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user

        for user in (viewer, UserFactory()):
            with pytest.raises(PermissionDenied):
                services.prepare_deposit(user=user, run=run, title="T")

    def test_nothing_is_saved_when_a_title_is_missing(self, editor, project):
        run, _members = sequenced(project)

        with pytest.raises(ValidationError):
            services.prepare_deposit(user=editor, run=run, title="  ")

        assert not project.deposits.exists()


class TestVoiding:
    def test_a_draft_is_voided_with_a_reason(self, editor, project):
        run, _members = sequenced(project)
        deposit = services.prepare_deposit(user=editor, run=run, title="T")

        services.void_record(user=editor, deposit=deposit, reason="Started by mistake")

        deposit.refresh_from_db()
        assert deposit.is_voided

    def test_a_deposit_being_submitted_cannot_be_voided(self, editor, project):
        from poresira.deposits.tests.factories import DepositFactory

        sending = DepositFactory(project=project, status=DepositStatus.SUBMITTING, **sent_fields())

        with pytest.raises(services.DepositServiceError, match="being submitted"):
            services.void_record(user=editor, deposit=sending, reason="No")
