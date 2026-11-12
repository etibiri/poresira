# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError

from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.samples.importing import FieldSampleImportError, import_field_samples, read_csv
from poresira.samples.models import CollectionEvent, Sample

pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return ProjectFactory()


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


def rows_of(*rows):
    return [dict(row) for row in rows]


def test_rows_with_the_same_collection_columns_share_one_event(project, editor):
    report = import_field_samples(
        user=editor,
        project=project,
        rows=rows_of(
            {"label": "A1", "locality": "Tiebele", "collected_on": "2016-04-15"},
            {"label": "A2", "locality": "Tiebele", "collected_on": "2016-04-15"},
            {"label": "B1", "locality": "Tiebele", "collected_on": "2016-04-16"},
        ),
    )

    assert (len(report.events), len(report.samples)) == (2, 3)
    assert report.samples[0].collection_event == report.samples[1].collection_event
    assert report.samples[0].collection_event != report.samples[2].collection_event


def test_values_are_converted_and_blank_cells_are_left_out(project, editor):
    report = import_field_samples(
        user=editor,
        project=project,
        rows=rows_of(
            {
                "label": "A1",
                "collected_on": "2016-04-15",
                "latitude": "11.08",
                "longitude": "-0.90",
                "province": "",
                "host_taxon": "Ipomoea batatas",
                "host_tax_id": "4120",
                "symptom_score": "5",
                "event_notes": "Farm visit",
            }
        ),
    )
    event, sample = report.events[0], report.samples[0]

    assert event.collected_on == date(2016, 4, 15)
    assert (event.latitude, event.longitude) == (Decimal("11.08"), Decimal("-0.90"))
    assert event.province == ""
    assert event.notes == "Farm visit"
    assert (sample.host_tax_id, sample.symptom_score) == (4120, 5)


def test_a_bad_row_refuses_the_whole_file_and_reports_every_problem(project, editor):
    rows = rows_of(
        {"label": "OK1"},
        {"label": "BAD1", "collected_on": "15/04/2016"},
        {"label": "BAD2", "latitude": "north"},
        {"label": "BAD3", "symptom_score": "12"},
        {"label": "OK2"},
    )

    with pytest.raises(FieldSampleImportError) as refusal:
        import_field_samples(user=editor, project=project, rows=rows)

    assert [problem.split(":")[0] for problem in refusal.value.problems] == [
        "line 3",
        "line 4",
        "line 5",
    ]
    assert not Sample.objects.exists()
    assert not CollectionEvent.objects.exists()


def test_a_duplicate_label_is_a_problem(project, editor):
    with pytest.raises(FieldSampleImportError, match="line 3"):
        import_field_samples(
            user=editor, project=project, rows=rows_of({"label": "A1"}, {"label": "A1"})
        )


def test_unknown_columns_are_refused_up_front(project, editor):
    with pytest.raises(FieldSampleImportError, match="unknown columns"):
        import_field_samples(
            user=editor, project=project, rows=rows_of({"label": "A1", "oops": "1"})
        )


def test_users_who_cannot_edit_the_project_cannot_import(project):
    with pytest.raises(PermissionDenied):
        import_field_samples(user=UserFactory(), project=project, rows=rows_of({"label": "A1"}))


def test_import_is_audited_with_the_importing_user(project, editor):
    report = import_field_samples(user=editor, project=project, rows=rows_of({"label": "A1"}))

    created = report.samples[0].events.get(pgh_label="insert")
    assert created.pgh_context.metadata["user"] == str(editor.pk)


class TestCommand:
    @pytest.fixture
    def csv_path(self, tmp_path):
        path = tmp_path / "samples.csv"
        path.write_text(
            "label,locality,collected_on\nBFA1,Bagre,2016-05-06\nBFA2,Bagre,2016-05-06\n",
            encoding="utf-8",
        )
        return path

    def run(self, csv_path, project, editor, *extra):
        output = StringIO()
        call_command(
            "import_field_samples",
            str(csv_path),
            "--project",
            project.code,
            "--user",
            editor.username,
            *extra,
            stdout=output,
        )
        return output.getvalue()

    def test_imports_a_file(self, csv_path, project, editor):
        message = self.run(csv_path, project, editor)

        assert "Imported 2 field sample(s) in 1 collection event(s)" in message
        assert Sample.objects.count() == 2

    def test_dry_run_validates_then_saves_nothing(self, csv_path, project, editor):
        message = self.run(csv_path, project, editor, "--dry-run")

        assert "Would import 2" in message
        assert not Sample.objects.exists()

    def test_a_refused_file_exits_with_the_problems(self, tmp_path, project, editor):
        path = tmp_path / "bad.csv"
        path.write_text("label,symptom_score\nBFA1,99\n", encoding="utf-8")
        errors = StringIO()

        with pytest.raises(CommandError, match="nothing saved"):
            call_command(
                "import_field_samples",
                str(path),
                "--project",
                project.code,
                "--user",
                editor.username,
                stderr=errors,
            )
        assert "line 2" in errors.getvalue()
        assert not Sample.objects.exists()

    def test_unknown_project_or_user_is_reported(self, csv_path, project, editor):
        with pytest.raises(CommandError):
            self.run(csv_path, ProjectFactory.build(code="nope"), editor)


def test_read_csv_accepts_a_byte_order_mark(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes("﻿label\nBFA1\n".encode())

    assert read_csv(path) == [{"label": "BFA1"}]
