# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.pcr.importing import PcrImportError, import_pcr_run
from poresira.pcr.models import Container, PcrRun, Result, Role, RunStatus
from poresira.pcr.tests.factories import AssayFactory
from poresira.samples.tests.factories import DerivativeFactory, SampleFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return ProjectFactory()


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


@pytest.fixture
def assay():
    return AssayFactory()


def run_import(project, editor, assay, rows, **options):
    return import_pcr_run(user=editor, project=project, assay=assay, rows=rows, **options)


def test_a_run_with_results_is_performed_and_can_be_closed(project, editor, assay):
    first, second = SampleFactory(project=project), SampleFactory(project=project)
    rows = [
        {"sample": first.identifier, "result": "positive", "band_size_bp": "700", "ct": "21.5"},
        {"sample": second.label, "result": "negative", "position": "c03"},
        {"role": "negative_control", "result": "negative"},
    ]

    report = run_import(project, editor, assay, rows, performed_on=date(2026, 3, 1), close=True)

    assert report.run.status == RunStatus.CLOSED
    assert [r.position for r in report.reactions] == ["A1", "C3", "B1"]
    assert report.reactions[0].band_size_bp == 700
    assert report.reactions[0].ct == Decimal("21.5")
    assert report.reactions[2].role == Role.NEGATIVE_CONTROL


def test_rows_without_results_leave_the_run_planned(project, editor, assay):
    report = run_import(project, editor, assay, [{"sample": SampleFactory(project=project).label}])

    assert report.run.status == RunStatus.PLANNED
    assert report.reactions[0].result == Result.PENDING


def test_tubes_are_numbered(project, editor, assay):
    samples = [SampleFactory(project=project) for _n in range(2)]

    report = run_import(
        project,
        editor,
        assay,
        [{"sample": s.label} for s in samples],
        container=Container.TUBES,
        tube_count=2,
    )

    assert [r.position for r in report.reactions] == ["1", "2"]


def test_a_reconstructed_run_needs_no_date(project, editor, assay):
    report = run_import(
        project,
        editor,
        assay,
        [{"sample": SampleFactory(project=project).label, "result": "positive"}],
        reconstructed=True,
    )

    assert report.run.status == RunStatus.PERFORMED
    assert report.run.performed_on is None


def test_every_problem_is_listed_and_nothing_is_saved(project, editor, assay):
    good = SampleFactory(project=project)
    rows = [
        {"sample": good.label, "result": "positive"},
        {"sample": "nobody", "result": "positive"},
        {"sample": good.label, "result": "maybe"},
        {"sample": good.label, "result": "positive", "role": "boss"},
        {"sample": good.label, "result": "positive", "ct": "abc"},
    ]

    with pytest.raises(PcrImportError) as error:
        run_import(project, editor, assay, rows, performed_on=date(2026, 3, 1))

    problems = error.value.problems
    assert [p.split(":")[0] for p in problems] == ["line 3", "line 4", "line 5", "line 6"]
    assert not PcrRun.objects.exists()


def test_problems_found_while_placing_reactions_roll_everything_back(project, editor, assay):
    sample = SampleFactory(project=project)
    rows = [
        {"sample": sample.label, "position": "A1"},
        {"sample": sample.label, "position": "A1"},
        {"sample": sample.label, "position": "Z9"},
    ]

    with pytest.raises(PcrImportError) as error:
        run_import(project, editor, assay, rows)

    assert len(error.value.problems) == 2
    assert not PcrRun.objects.exists()


def test_an_ambiguous_label_asks_for_the_identifier(project, editor, assay):
    first = SampleFactory(project=project, label="DUP")
    DerivativeFactory(parent=first, label="DUP")

    with pytest.raises(PcrImportError, match="several samples"):
        run_import(project, editor, assay, [{"sample": "DUP"}])


def test_a_sample_of_another_project_is_not_found(project, editor, assay):
    outsider = SampleFactory()

    with pytest.raises(PcrImportError, match="no sample"):
        run_import(project, editor, assay, [{"sample": outsider.identifier}])


def test_results_must_come_for_all_rows_or_none(project, editor, assay):
    rows = [
        {"sample": SampleFactory(project=project).label, "result": "positive"},
        {"sample": SampleFactory(project=project).label},
    ]

    with pytest.raises(PcrImportError, match="every row"):
        run_import(project, editor, assay, rows, performed_on=date(2026, 3, 1))


def test_unknown_columns_are_refused(project, editor, assay):
    with pytest.raises(PcrImportError, match="unknown columns"):
        run_import(project, editor, assay, [{"sample": "x", "colour": "red"}])


class TestCommand:
    @pytest.fixture
    def csv_path(self, tmp_path, project):
        sample = SampleFactory(project=project, label="BFA7")
        path = tmp_path / "run.csv"
        path.write_text(f"sample,result\n{sample.identifier},positive\n", encoding="utf-8")
        return path

    def call(self, csv_path, project, editor, assay, *extra):
        output = StringIO()
        call_command(
            "import_pcr_run",
            str(csv_path),
            "--project",
            project.code,
            "--assay",
            assay.name,
            "--user",
            editor.username,
            *extra,
            stdout=output,
        )
        return output.getvalue()

    def test_it_creates_a_run(self, csv_path, project, editor, assay):
        text = self.call(csv_path, project, editor, assay, "--performed-on", "2026-03-01")

        assert "Created run" in text
        assert PcrRun.objects.get().status == RunStatus.PERFORMED

    def test_a_dry_run_saves_nothing(self, csv_path, project, editor, assay):
        text = self.call(
            csv_path, project, editor, assay, "--performed-on", "2026-03-01", "--dry-run"
        )

        assert "Would create" in text
        assert not PcrRun.objects.exists()

    def test_refusals_are_explained(self, csv_path, project, editor, assay):
        with pytest.raises(CommandError, match="nothing saved"):
            self.call(csv_path, project, editor, assay)  # results given but no date

    def test_unknown_names_are_reported(self, csv_path, project, editor, assay):
        with pytest.raises(CommandError):
            call_command(
                "import_pcr_run", str(csv_path), "--project", "nope", "--assay", "x", "--user", "u"
            )
