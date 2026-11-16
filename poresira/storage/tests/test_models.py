# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.db import DatabaseError, IntegrityError, transaction

from poresira.core.audit import verify_audit_chain, with_reason
from poresira.core.identifiers import is_valid_identifier
from poresira.core.tests.factories import ProjectFactory, UserFactory
from poresira.core.voiding import HardDeleteForbiddenError
from poresira.pcr.tests.factories import PcrRunFactory
from poresira.samples.tests.factories import SampleFactory
from poresira.storage.models import (
    DATA_FILE_TYPE_CODE,
    BackendKind,
    DataFile,
    FixityCheck,
    Integrity,
    StorageBackend,
    Tier,
)
from poresira.storage.tests.factories import (
    DataFileFactory,
    FixityCheckFactory,
    StorageBackendFactory,
)

pytestmark = pytest.mark.django_db


def refuses(error, action):
    with pytest.raises(error), transaction.atomic():
        action()


class TestBackends:
    def test_a_disk_or_share_needs_an_absolute_root_and_a_medium_needs_a_label(self):
        StorageBackendFactory(kind=BackendKind.NAS, root="/mnt/nas")
        StorageBackendFactory(kind=BackendKind.OFFLINE, root="Tape 3, shelf B")

        refuses(IntegrityError, lambda: StorageBackendFactory(root="relative/dir"))
        refuses(IntegrityError, lambda: StorageBackendFactory(root=""))
        refuses(IntegrityError, lambda: StorageBackendFactory(kind=BackendKind.OFFLINE, root=""))

    def test_a_name_is_unique_among_active_backends(self):
        StorageBackendFactory(name="Main disk")

        refuses(IntegrityError, lambda: StorageBackendFactory(name="Main disk"))

    def test_the_kind_is_fixed_and_moving_the_root_needs_a_reason(self):
        backend = StorageBackendFactory(root="/data/a")

        backend.kind = BackendKind.NAS
        refuses(DatabaseError, backend.save)
        backend.refresh_from_db()
        backend.root = "/data/b"
        refuses(DatabaseError, backend.save)
        with with_reason("The disk was remounted"):
            backend.save()

    def test_only_a_disk_or_share_can_be_read(self):
        assert StorageBackendFactory().is_readable
        assert not StorageBackendFactory(kind=BackendKind.OFFLINE, root="Tape").is_readable


class TestDataFiles:
    def test_a_file_gets_a_valid_identifier_and_starts_hot_and_unchecked(self):
        file = DataFileFactory()

        assert file.identifier.split("-")[1].startswith(DATA_FILE_TYPE_CODE)
        assert is_valid_identifier(file.identifier)
        assert (file.tier, file.integrity) == (Tier.HOT, Integrity.UNCHECKED)

    def test_a_place_holds_one_active_file(self):
        first = DataFileFactory(path="a/reads.fastq.gz")

        refuses(
            IntegrityError,
            lambda: DataFileFactory(
                project=first.project,
                backend=first.backend,
                path="a/reads.fastq.gz",
                sequencing_run=first.sequencing_run,
            ),
        )
        first.void(user=UserFactory(), reason="Moved")
        DataFileFactory(
            project=first.project,
            backend=first.backend,
            path="a/reads.fastq.gz",
            sequencing_run=first.sequencing_run,
        )

    @pytest.mark.parametrize("path", ["/abs/file", "a/../b", "../x", "a/.."])
    def test_a_path_is_relative_and_stays_inside_the_backend(self, path):
        refuses(IntegrityError, lambda: DataFileFactory(path=path))

    @pytest.mark.parametrize(
        ("name", "value"),
        [("sha256", "abc"), ("sha256", "G" * 64), ("md5", "A" * 32), ("md5", "0" * 31)],
    )
    def test_checksums_are_lowercase_hexadecimal_of_the_right_length(self, name, value):
        refuses(IntegrityError, lambda: DataFileFactory(**{name: value}))

    def test_a_file_is_attached_to_exactly_one_record_of_its_project(self):
        project = ProjectFactory()
        DataFileFactory(
            project=project, sequencing_run=None, pcr_run=PcrRunFactory(project=project)
        )
        DataFileFactory(project=project, sequencing_run=None, sample=SampleFactory(project=project))

        refuses(IntegrityError, lambda: DataFileFactory(project=project, sequencing_run=None))
        refuses(
            IntegrityError,
            lambda: DataFileFactory(
                project=project,
                pcr_run=PcrRunFactory(project=project),
                sample=SampleFactory(project=project),
            ),
        )

    def test_the_record_must_belong_to_the_project_of_the_file(self):
        project = ProjectFactory()

        refuses(
            DatabaseError,
            lambda: DataFileFactory(project=project, sequencing_run=None, pcr_run=PcrRunFactory()),
        )
        refuses(
            DatabaseError,
            lambda: DataFileFactory(project=project, sequencing_run=None, sample=SampleFactory()),
        )

    @pytest.mark.parametrize(
        ("name", "value"),
        [("path", "other.fastq"), ("size_bytes", 5), ("sha256", "1" * 64), ("kind", "bam")],
    )
    def test_what_identifies_the_content_cannot_change(self, name, value):
        file = DataFileFactory()

        setattr(file, name, value)
        refuses(DatabaseError, file.save)

    def test_the_tier_changes_only_with_a_reason(self):
        file = DataFileFactory()

        file.tier = Tier.COLD
        refuses(DatabaseError, file.save)
        file.refresh_from_db()
        file.tier = Tier.COLD
        with with_reason("Archived to tape"):
            file.save()

    def test_the_integrity_is_updated_without_a_reason(self):
        file = DataFileFactory()

        file.integrity = Integrity.INTACT
        file.save()

    def test_a_file_is_voided_never_deleted(self):
        file = DataFileFactory()

        with pytest.raises(HardDeleteForbiddenError):
            DataFile.objects.filter(pk=file.pk).delete()
        with pytest.raises(HardDeleteForbiddenError):
            file.delete()
        refuses(DatabaseError, lambda: DataFile.objects.filter(pk=file.pk)._raw_delete("default"))


class TestChecks:
    def test_a_check_records_what_was_observed_and_is_never_edited(self):
        check = FixityCheckFactory(outcome=Integrity.MISMATCH, observed_sha256="a" * 64)

        check.outcome = Integrity.INTACT
        refuses(DatabaseError, check.save)

    def test_a_check_has_a_real_outcome(self):
        refuses(IntegrityError, lambda: FixityCheckFactory(outcome=Integrity.UNCHECKED))

    def test_checks_accumulate_on_a_file(self):
        file = DataFileFactory()
        FixityCheckFactory(file=file)
        FixityCheckFactory(file=file, outcome=Integrity.MISSING)

        assert file.checks.count() == 2


def test_changes_are_audited_and_the_chain_is_intact():
    file = DataFileFactory()
    with with_reason("Archived"):
        file.tier = Tier.WARM
        file.save()
    FixityCheckFactory(file=file)

    assert verify_audit_chain().is_intact
    assert file.events.filter(pgh_label="insert").count() == 1
    assert file.events.filter(pgh_label="update").count() == 1
    assert StorageBackend.objects.count() == 1 and FixityCheck.objects.count() == 1
