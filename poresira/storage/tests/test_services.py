# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from poresira.core.models import GlobalRole, ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.pcr.tests.factories import PcrRunFactory
from poresira.samples.tests.factories import SampleFactory
from poresira.sequencing.tests.factories import SequencingRunFactory
from poresira.storage import services
from poresira.storage.backends import DiskStore, FileNotReadableError, OfflineStore, store_for
from poresira.storage.kinds import classify
from poresira.storage.models import BackendKind, FileKind, FixityCheck, Integrity, Tier
from poresira.storage.tests.factories import DataFileFactory, StorageBackendFactory

pytestmark = pytest.mark.django_db

CONTENT = b"ACGT" * 1000


@pytest.fixture
def project():
    return ProjectFactory()


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


@pytest.fixture
def staff():
    return UserFactory(role=GlobalRole.PLATFORM_MANAGER)


@pytest.fixture
def disk(tmp_path):
    (tmp_path / "run1").mkdir()
    (tmp_path / "run1" / "reads.fastq.gz").write_bytes(CONTENT)
    return StorageBackendFactory(root=str(tmp_path))


@pytest.fixture
def sequencing_run(project):
    return SequencingRunFactory(library__project=project)


class TestKinds:
    @pytest.mark.parametrize(
        ("path", "kind"),
        [
            ("a/reads.fastq.gz", FileKind.FASTQ),
            ("a/reads.FQ", FileKind.FASTQ),
            ("a/signal.pod5", FileKind.POD5),
            ("a/signal.fast5", FileKind.FAST5),
            ("a/calls.bam", FileKind.BAM),
            ("a/calls.bam.bai", FileKind.OTHER),
            ("report_run.pdf", FileKind.REPORT),
            ("Gel1.jpg", FileKind.GEL_IMAGE),
            ("notes", FileKind.OTHER),
        ],
    )
    def test_a_file_is_classified_from_its_name(self, path, kind):
        assert classify(path) == kind


class TestStores:
    def test_a_disk_store_hashes_size_sha256_and_md5_in_one_read(self, disk):
        fingerprint = DiskStore(disk.root).fingerprint("run1/reads.fastq.gz")

        assert fingerprint.size_bytes == len(CONTENT)
        assert fingerprint.sha256 == hashlib.sha256(CONTENT).hexdigest()
        assert fingerprint.md5 == hashlib.md5(CONTENT).hexdigest()  # noqa: S324

    def test_a_disk_store_does_not_leave_its_root(self, disk, tmp_path):
        outside = tmp_path.parent / "secret.txt"
        outside.write_bytes(b"x")
        (tmp_path / "link").symlink_to(outside)

        with pytest.raises(PermissionError):
            DiskStore(disk.root).fingerprint("link")
        with pytest.raises(PermissionError):
            DiskStore(disk.root).fingerprint("../secret.txt")

    def test_an_offline_store_reads_nothing(self):
        with pytest.raises(FileNotReadableError):
            OfflineStore().fingerprint("a")

    def test_the_store_follows_the_kind_of_the_backend(self):
        nas = StorageBackendFactory(kind=BackendKind.NAS, root="/mnt/nas")
        tape = StorageBackendFactory(kind=BackendKind.OFFLINE, root="Tape")

        assert isinstance(store_for(nas), DiskStore)
        assert isinstance(store_for(tape), OfflineStore)


class TestBackends:
    def test_platform_staff_create_a_backend_on_an_existing_directory(self, staff, tmp_path):
        backend = services.create_backend(
            user=staff, name="Lab disk", kind=BackendKind.LOCAL, root=f"{tmp_path}/"
        )

        assert backend.root == str(tmp_path)

    def test_a_missing_or_relative_directory_is_refused(self, staff, tmp_path):
        for root in (str(tmp_path / "absent"), "relative", ""):
            with pytest.raises(ValidationError):
                services.create_backend(user=staff, name="x", kind=BackendKind.NAS, root=root)

    def test_an_offline_medium_needs_only_a_label(self, staff):
        backend = services.create_backend(
            user=staff, name="Tape 3", kind=BackendKind.OFFLINE, root="Tape 3, shelf B"
        )

        assert not backend.is_readable

    def test_ordinary_users_cannot_create_backends(self, editor, tmp_path):
        with pytest.raises(PermissionDenied):
            services.create_backend(
                user=editor, name="x", kind=BackendKind.LOCAL, root=str(tmp_path)
            )

    def test_moving_a_root_needs_a_reason(self, staff, disk, tmp_path):
        other = tmp_path / "moved"
        other.mkdir()

        with pytest.raises(ValueError, match="reason"):
            services.move_backend(user=staff, backend=disk, root=str(other), reason=" ")
        services.move_backend(user=staff, backend=disk, root=str(other), reason="Remounted")
        disk.refresh_from_db()
        assert disk.root == str(other)


class TestRegistering:
    def test_a_file_is_hashed_where_it_is_and_starts_intact(
        self, editor, project, disk, sequencing_run
    ):
        file = services.register_file(
            user=editor,
            project=project,
            backend=disk,
            path="run1/reads.fastq.gz",
            sequencing_run=sequencing_run,
        )

        assert file.kind == FileKind.FASTQ
        assert file.size_bytes == len(CONTENT)
        assert file.sha256 == hashlib.sha256(CONTENT).hexdigest()
        assert (file.integrity, file.tier) == (Integrity.INTACT, Tier.HOT)
        assert file.last_checked_at is not None and file.registered_by == editor

    def test_the_kind_can_be_given(self, editor, project, disk, sequencing_run):
        file = services.register_file(
            user=editor,
            project=project,
            backend=disk,
            path="run1/reads.fastq.gz",
            sequencing_run=sequencing_run,
            kind=FileKind.OTHER,
        )

        assert file.kind == FileKind.OTHER

    @pytest.mark.parametrize("path", ["", "/etc/passwd", "../x", "a/../../x"])
    def test_a_path_leaving_the_backend_is_refused(
        self, editor, project, disk, sequencing_run, path
    ):
        with pytest.raises(ValidationError):
            services.register_file(
                user=editor, project=project, backend=disk, path=path, sequencing_run=sequencing_run
            )

    def test_a_missing_file_is_refused(self, editor, project, disk, sequencing_run):
        with pytest.raises(ValidationError, match="No such file"):
            services.register_file(
                user=editor,
                project=project,
                backend=disk,
                path="run1/absent.pod5",
                sequencing_run=sequencing_run,
            )

    def test_one_link_is_needed_and_it_must_belong_to_the_project(
        self, editor, project, disk, sequencing_run
    ):
        sample = SampleFactory(project=project)
        arguments = {
            "user": editor,
            "project": project,
            "backend": disk,
            "path": "run1/reads.fastq.gz",
        }

        with pytest.raises(ValidationError):
            services.register_file(**arguments)
        with pytest.raises(ValidationError):
            services.register_file(**arguments, sequencing_run=sequencing_run, sample=sample)
        with pytest.raises(ValidationError, match="project"):
            services.register_file(**arguments, pcr_run=PcrRunFactory())
        services.register_file(**arguments, pcr_run=PcrRunFactory(project=project))

    def test_a_voided_record_receives_no_file(self, editor, project, disk):
        sample = SampleFactory(project=project)
        sample.void(user=editor, reason="Wrong sample")

        with pytest.raises(ValidationError, match="voided"):
            services.register_file(
                user=editor,
                project=project,
                backend=disk,
                path="run1/reads.fastq.gz",
                sample=sample,
            )

    def test_readers_and_strangers_cannot_register(self, project, disk, sequencing_run):
        viewer = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user

        for user in (viewer, UserFactory()):
            with pytest.raises(PermissionDenied):
                services.register_file(
                    user=user,
                    project=project,
                    backend=disk,
                    path="run1/reads.fastq.gz",
                    sequencing_run=sequencing_run,
                )

    def test_a_file_on_an_offline_medium_comes_with_its_checksums(
        self, editor, project, sequencing_run
    ):
        tape = StorageBackendFactory(kind=BackendKind.OFFLINE, root="Tape 3")
        arguments = {
            "user": editor,
            "project": project,
            "backend": tape,
            "path": "run1/signal.pod5",
            "sequencing_run": sequencing_run,
        }

        with pytest.raises(ValidationError):
            services.register_file(**arguments)
        with pytest.raises(ValidationError):
            services.register_file(**arguments, size_bytes=10, sha256="zz", md5="0" * 32)
        file = services.register_file(**arguments, size_bytes=10, sha256="A" * 64, md5="b" * 32)

        assert file.sha256 == "a" * 64
        assert (file.integrity, file.last_checked_at) == (Integrity.UNCHECKED, None)


class TestChecking:
    @pytest.fixture
    def file(self, editor, project, disk, sequencing_run):
        return services.register_file(
            user=editor,
            project=project,
            backend=disk,
            path="run1/reads.fastq.gz",
            sequencing_run=sequencing_run,
        )

    def test_an_untouched_file_is_intact(self, editor, file):
        check = services.verify_file(file=file, user=editor)

        assert check.outcome == Integrity.INTACT
        file.refresh_from_db()
        assert file.integrity == Integrity.INTACT and file.checks.count() == 1

    def test_a_changed_file_is_reported_with_what_was_seen(self, editor, file, tmp_path):
        (tmp_path / "run1" / "reads.fastq.gz").write_bytes(CONTENT + b"A")

        check = services.verify_file(file=file, user=editor)

        assert check.outcome == Integrity.MISMATCH
        assert check.observed_size_bytes == len(CONTENT) + 1
        assert check.observed_sha256 != file.sha256
        file.refresh_from_db()
        assert file.integrity == Integrity.MISMATCH
        assert file.sha256 == hashlib.sha256(CONTENT).hexdigest()  # the record is not rewritten

    def test_a_file_that_vanished_is_missing(self, editor, file, tmp_path):
        (tmp_path / "run1" / "reads.fastq.gz").unlink()

        assert services.verify_file(file=file, user=editor).outcome == Integrity.MISSING

    def test_an_unreadable_file_is_reported(self, editor, file, tmp_path):
        target = tmp_path / "run1" / "reads.fastq.gz"
        target.chmod(0)
        try:
            outcome = services.verify_file(file=file, user=editor).outcome
        finally:
            target.chmod(0o644)

        assert outcome in (Integrity.UNREADABLE, Integrity.INTACT)  # root can read anything

    def test_a_scheduled_check_has_no_user(self, file):
        check = services.verify_file(file=file)

        assert check.checked_by is None and check.outcome == Integrity.INTACT

    def test_readers_cannot_start_a_check_and_offline_files_cannot_be_checked(self, project, file):
        viewer = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user
        with pytest.raises(PermissionDenied):
            services.verify_file(file=file, user=viewer)

        tape = DataFileFactory(
            project=project, backend=StorageBackendFactory(kind=BackendKind.OFFLINE, root="T")
        )
        with pytest.raises(FileNotReadableError):
            services.verify_file(file=tape)

    def test_every_check_is_kept(self, editor, file):
        services.verify_file(file=file, user=editor)
        services.verify_file(file=file, user=editor)

        assert FixityCheck.objects.filter(file=file).count() == 2


class TestTierAndVoiding:
    def test_the_tier_changes_with_a_reason(self, editor, project):
        file = DataFileFactory(project=project)

        services.change_tier(user=editor, file=file, tier=Tier.COLD, reason="Archived")
        file.refresh_from_db()
        assert file.tier == Tier.COLD
        with pytest.raises(ValueError, match="reason"):
            services.change_tier(user=editor, file=file, tier=Tier.WARM, reason="")
        with pytest.raises(ValidationError):
            services.change_tier(user=editor, file=file, tier="frozen", reason="Because")

    def test_a_file_is_voided_with_a_reason_and_its_place_is_freed(self, editor, project):
        file = DataFileFactory(project=project)

        services.void_record(user=editor, record=file, reason="Wrong file")

        file.refresh_from_db()
        assert file.is_voided

    def test_a_backend_with_active_files_cannot_be_voided(self, staff, editor, project):
        file = DataFileFactory(project=project)

        with pytest.raises(services.RecordInUseError):
            services.void_record(user=staff, record=file.backend, reason="Retired")
        services.void_record(user=editor, record=file, reason="Moved elsewhere")
        services.void_record(user=staff, record=file.backend, reason="Retired")

        file.backend.refresh_from_db()
        assert file.backend.is_voided

    def test_ordinary_users_cannot_void_a_backend(self, editor, project):
        with pytest.raises(PermissionDenied):
            services.void_record(user=editor, record=StorageBackendFactory(), reason="No")
