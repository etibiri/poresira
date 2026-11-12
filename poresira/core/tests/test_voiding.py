# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from poresira.core.models import Project, ProjectEvent
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.core.voiding import HardDeleteForbiddenError

pytestmark = pytest.mark.django_db


def test_void_records_user_time_and_reason():
    project, user = ProjectFactory(), UserFactory()

    project.void(user=user, reason="  Created by mistake  ")
    project.refresh_from_db()

    assert project.is_voided
    assert project.voided_by == user
    assert project.void_reason == "Created by mistake"
    assert project.voided_at is not None


@pytest.mark.parametrize("reason", ["", "   "])
def test_void_requires_a_reason(reason):
    project = ProjectFactory()

    with pytest.raises(ValueError, match="reason"):
        project.void(user=UserFactory(), reason=reason)

    assert not Project.objects.get(pk=project.pk).is_voided


def test_voiding_twice_is_refused():
    project, user = ProjectFactory(), UserFactory()
    project.void(user=user, reason="First")

    with pytest.raises(ValueError, match="already"):
        project.void(user=user, reason="Second")


def test_void_reason_is_written_to_the_audit_entry():
    project = ProjectFactory()
    project.void(user=UserFactory(), reason="Duplicate")

    update_event = ProjectEvent.objects.get(pgh_obj=project, pgh_label="update")

    assert update_event.pgh_context.metadata["reason"] == "Duplicate"
    assert update_event.void_reason == "Duplicate"


def test_instance_and_queryset_delete_are_forbidden():
    project = ProjectFactory()

    with pytest.raises(HardDeleteForbiddenError):
        project.delete()
    with pytest.raises(HardDeleteForbiddenError):
        Project.objects.all().delete()


def test_database_blocks_raw_delete():
    ProjectFactory()

    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM core_project")


def test_database_rejects_void_without_reason():
    project = ProjectFactory()

    with pytest.raises(IntegrityError), transaction.atomic():
        Project.objects.filter(pk=project.pk).update(voided_at="2026-01-01T00:00:00Z")


def test_active_excludes_voided_records():
    kept, voided = ProjectFactory(), ProjectFactory()
    voided.void(user=UserFactory(), reason="Obsolete")

    assert list(Project.objects.active()) == [kept]


def test_voided_membership_no_longer_grants_visibility():
    membership = ProjectMembershipFactory()
    member, project = membership.user, membership.project
    assert Project.objects.visible_to(member).filter(pk=project.pk).exists()

    membership.void(user=UserFactory(), reason="Left the project")

    assert not Project.objects.visible_to(member).exists()


def test_user_can_be_added_again_after_membership_was_voided():
    membership = ProjectMembershipFactory()
    membership.void(user=UserFactory(), reason="Left")

    ProjectMembershipFactory(project=membership.project, user=membership.user)
