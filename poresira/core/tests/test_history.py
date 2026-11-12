# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pytest

from poresira.core.audit import with_reason
from poresira.core.history import HistorySource, build_history, project_history
from poresira.core.models import ProjectEvent
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory

pytestmark = pytest.mark.django_db


def project_source(project, **options):
    return HistorySource(
        events=ProjectEvent.objects.filter(pgh_obj=project).order_by("pgh_created_at", "pgh_id"),
        field_names=("name", "code"),
        subject_for=lambda event: "Project",
        **options,
    )


def test_entries_are_newest_first_with_before_and_after_values():
    project = ProjectFactory(name="Alpha")
    project.name = "Beta"
    project.save()

    entries = build_history([project_source(project)])

    assert [entry.action for entry in entries] == ["Modified", "Created"]
    assert ("Name", "Alpha", "Beta") in entries[0].changes
    assert all(change[0] != "Code" for change in entries[0].changes)
    assert ("Name", None, "Alpha") in entries[1].changes


def test_labels_fall_back_to_the_model_field_name_and_can_be_overridden():
    project = ProjectFactory(name="Alpha")

    default_labels = build_history([project_source(project)])[0].changes
    overridden = build_history([project_source(project, labels={"name": "Titre"})])[0].changes

    assert {label for label, *_ in default_labels} == {"Name", "Code"}
    assert "Titre" in {label for label, *_ in overridden}


def test_user_and_reason_come_from_the_audit_context():
    user = UserFactory()
    project = ProjectFactory()
    project.name = "Renamed"
    with (
        pghistory.context(user=str(user.pk), ip_address="10.0.0.5", request_id="req-1"),
        with_reason("Typo"),
    ):
        project.save()

    newest = build_history([project_source(project)])[0]

    assert newest.user == user
    assert (newest.reason, newest.ip_address, newest.request_id) == ("Typo", "10.0.0.5", "req-1")


def test_unknown_user_in_context_is_shown_as_nobody():
    project = ProjectFactory()
    project.name = "Renamed"
    with pghistory.context(user="00000000-0000-0000-0000-000000000000"):
        project.save()

    assert build_history([project_source(project)])[0].user is None


def test_several_sources_are_merged_chronologically_and_limited():
    project = ProjectFactory()
    ProjectMembershipFactory(project=project)

    entries = project_history(project)

    assert sorted(entry.subject.split(":")[0] for entry in entries) == ["Membership", "Project"]
    assert len(build_history([project_source(project)] * 3, limit=2)) == 2
