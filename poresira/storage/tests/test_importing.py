# SPDX-License-Identifier: AGPL-3.0-or-later
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from poresira.core.models import GlobalRole, ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.sequencing.tests.factories import SequencingRunFactory
from poresira.storage.importing import DirectoryError, register_directory
from poresira.storage.models import DataFile, FileKind
from poresira.storage.tests.factories import StorageBackendFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return ProjectFactory()


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


@pytest.fixture
def run(project):
    return SequencingRunFactory(library__project=project)


@pytest.fixture
def layout(tmp_path):
    """A run folder as MinKNOW writes it, below a backend root."""
    folder = tmp_path / "runs" / "run1"
    for name in ("fastq_pass/a.fastq.gz", "fastq_pass/b.fastq.gz", "pod5/x.pod5", "report.pdf"):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(name.encode() * 10)
    (folder / ".hidden").write_bytes(b"x")
    return folder


@pytest.fixture
def backend(tmp_path):
    return StorageBackendFactory(root=str(tmp_path))


def register(editor, project, backend, run, folder, **options):
    return register_directory(
        user=editor,
        project=project,
        backend=backend,
        directory=folder,
        link={"sequencing_run": run},
        **options,
    )


def test_every_visible_file_is_registered_with_its_kind_and_path(
    editor, project, backend, run, layout
):
    report = register(editor, project, backend, run, layout)

    assert len(report.registered) == 4 and not report.failed
    assert {f.path for f in report.registered} == {
        "runs/run1/fastq_pass/a.fastq.gz",
        "runs/run1/fastq_pass/b.fastq.gz",
        "runs/run1/pod5/x.pod5",
        "runs/run1/report.pdf",
    }
    kinds = {f.kind for f in report.registered}
    assert kinds == {FileKind.FASTQ, FileKind.POD5, FileKind.REPORT}
    assert all(f.sequencing_run == run for f in report.registered)


def test_a_second_pass_skips_what_is_registered_and_picks_up_what_is_new(
    editor, project, backend, run, layout
):
    register(editor, project, backend, run, layout)
    (layout / "pod5" / "y.pod5").write_bytes(b"new")

    report = register(editor, project, backend, run, layout)

    assert [f.path for f in report.registered] == ["runs/run1/pod5/y.pod5"]
    assert report.already_registered == 4
    assert DataFile.objects.count() == 5


def test_only_the_wanted_kinds_are_registered(editor, project, backend, run, layout):
    report = register(editor, project, backend, run, layout, only_kinds={FileKind.POD5})

    assert [f.kind for f in report.registered] == [FileKind.POD5]
    assert report.filtered_out == 3


def test_a_dry_run_reads_and_registers_nothing(editor, project, backend, run, layout):
    report = register(editor, project, backend, run, layout, dry_run=True)

    assert report.would_register == 4 and report.would_register_bytes > 0
    assert not DataFile.objects.exists()


def test_a_directory_outside_the_backend_is_refused(
    editor, project, backend, run, tmp_path_factory
):
    elsewhere = tmp_path_factory.mktemp("elsewhere")

    with pytest.raises(DirectoryError, match="not inside"):
        register(editor, project, backend, run, elsewhere)
    with pytest.raises(DirectoryError, match="not a directory"):
        register(editor, project, backend, run, elsewhere / "absent")


def test_a_file_that_cannot_be_registered_does_not_stop_the_others(
    editor, project, backend, run, layout, tmp_path
):
    outside = tmp_path.parent / "outside.txt"
    outside.write_bytes(b"x")
    (layout / "link.txt").symlink_to(outside)

    report = register(editor, project, backend, run, layout)

    assert len(report.registered) == 4
    assert [path for path, _problem in report.failed] == ["runs/run1/link.txt"]


def test_readers_register_nothing(project, backend, run, layout):
    viewer = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user

    report = register(viewer, project, backend, run, layout)

    assert not report.registered and len(report.failed) == 4


def test_an_offline_medium_cannot_be_scanned(editor, project, run, layout):
    tape = StorageBackendFactory(kind="offline", root="Tape")

    with pytest.raises(DirectoryError, match="offline"):
        register(editor, project, tape, run, layout)


class TestCommands:
    def test_a_backend_is_declared_then_a_run_folder_registered(
        self, editor, run, layout, tmp_path
    ):
        admin = UserFactory(role=GlobalRole.PLATFORM_MANAGER)
        out = StringIO()
        call_command(
            "create_storage_backend",
            "Test disk",
            "--kind=local",
            f"--root={tmp_path}",
            f"--user={admin.username}",
            stdout=out,
        )
        arguments = [
            str(layout),
            "--backend=Test disk",
            f"--user={editor.username}",
            f"--sequencing-run={run.identifier}",
        ]

        call_command("register_files", *arguments, "--dry-run", stdout=out)
        assert "Would register 4 file(s)" in out.getvalue()
        assert not DataFile.objects.exists()

        call_command("register_files", *arguments, stdout=out)
        assert DataFile.objects.count() == 4
        assert "Registered 4 file(s)" in out.getvalue()

    def test_the_command_needs_exactly_one_link_and_known_names(self, editor, run, layout, backend):
        base = [str(layout), f"--backend={backend.name}", f"--user={editor.username}"]

        with pytest.raises(CommandError, match="exactly one"):
            call_command("register_files", *base)
        with pytest.raises(CommandError, match="No sequencing run"):
            call_command("register_files", *base, "--sequencing-run=WBF-Q26-99999-0")
        with pytest.raises(CommandError, match="Unknown kind"):
            call_command(
                "register_files", *base, f"--sequencing-run={run.identifier}", "--only=zip"
            )

    def test_failures_are_listed_and_the_command_fails(
        self, editor, run, layout, backend, tmp_path
    ):
        outside = tmp_path.parent / "outside2.txt"
        outside.write_bytes(b"x")
        (layout / "link.txt").symlink_to(outside)
        errors = StringIO()

        with pytest.raises(CommandError, match="could not be registered"):
            call_command(
                "register_files",
                str(layout),
                f"--backend={backend.name}",
                f"--user={editor.username}",
                f"--sequencing-run={run.identifier}",
                stdout=StringIO(),
                stderr=errors,
            )

        assert "link.txt" in errors.getvalue()
        assert DataFile.objects.count() == 4

    def test_only_platform_staff_declare_backends(self, editor, tmp_path):
        with pytest.raises(CommandError, match="may not declare"):
            call_command(
                "create_storage_backend",
                "x",
                "--kind=local",
                f"--root={tmp_path}",
                f"--user={editor.username}",
            )
