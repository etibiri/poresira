# SPDX-License-Identifier: AGPL-3.0-or-later
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.samples.models import SampleKind
from poresira.samples.tests.factories import DerivativeFactory, SampleFactory
from poresira.sequencing.importing import LibraryImportError, import_library
from poresira.sequencing.models import Library, LibraryStatus
from poresira.sequencing.tests.factories import LibraryKitFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return ProjectFactory()


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


@pytest.fixture
def kit():
    return LibraryKitFactory()


def product(project):
    return DerivativeFactory(
        kind=SampleKind.AMPLIFICATION_PRODUCT, parent=SampleFactory(project=project)
    )


def test_samples_are_put_under_their_barcodes_and_the_library_can_be_sealed(project, editor, kit):
    first, second = product(project), product(project)
    rows = [
        {"sample": first.identifier, "barcode": "BC01", "input_ng": "200"},
        {"sample": second.identifier, "barcode": "02"},
    ]

    report = import_library(
        user=editor, project=project, kit=kit, rows=rows, seal=True, name="Run 1"
    )

    assert report.library.status == LibraryStatus.SEALED
    assert [m.barcode for m in report.members] == [1, 2]
    assert report.members[0].input_ng == 200


def test_one_bad_row_saves_nothing(project, editor, kit):
    rows = [
        {"sample": product(project).identifier, "barcode": "1"},
        {"sample": "nobody", "barcode": "2"},
    ]

    with pytest.raises(LibraryImportError) as error:
        import_library(user=editor, project=project, kit=kit, rows=rows)

    assert [p[:6] for p in error.value.problems] == ["line 3"]
    assert not Library.objects.exists()


def test_a_barcode_outside_the_kit_saves_nothing(project, editor, kit):
    rows = [{"sample": product(project).identifier, "barcode": "200"}]

    with pytest.raises(LibraryImportError, match="line 2"):
        import_library(user=editor, project=project, kit=kit, rows=rows)

    assert not Library.objects.exists()


def test_unknown_columns_and_empty_files_are_refused(project, editor, kit):
    with pytest.raises(LibraryImportError, match="unknown columns"):
        import_library(user=editor, project=project, kit=kit, rows=[{"flowcell": "x"}])
    with pytest.raises(LibraryImportError, match="no rows"):
        import_library(user=editor, project=project, kit=kit, rows=[])


def test_a_repeated_barcode_is_refused(project, editor, kit):
    rows = [
        {"sample": product(project).identifier, "barcode": "5"},
        {"sample": product(project).identifier, "barcode": "NB05"},
    ]

    with pytest.raises(LibraryImportError, match="line 3"):
        import_library(user=editor, project=project, kit=kit, rows=rows)


def test_the_command_reads_a_file_and_a_dry_run_saves_nothing(project, editor, kit, tmp_path):
    sample = product(project)
    path = tmp_path / "library.csv"
    path.write_text(f"sample,barcode\n{sample.identifier},BC03\n")
    arguments = [str(path), "--project", project.code, "--kit", kit.code, "--user", editor.username]

    out = StringIO()
    call_command("import_library", *arguments, "--dry-run", stdout=out)
    assert "Would create" in out.getvalue()
    assert not Library.objects.exists()

    call_command("import_library", *arguments, "--seal", stdout=out)
    assert Library.objects.get().status == LibraryStatus.SEALED


def test_the_command_refuses_a_bad_file(project, editor, kit, tmp_path):
    path = tmp_path / "library.csv"
    path.write_text("sample,barcode\nnobody,1\n")

    with pytest.raises(CommandError, match="nothing saved"):
        call_command(
            "import_library",
            str(path),
            "--project",
            project.code,
            "--kit",
            kit.code,
            "--user",
            editor.username,
            stderr=StringIO(),
        )
