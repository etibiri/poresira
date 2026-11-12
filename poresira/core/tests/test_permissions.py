# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.contrib.auth.models import AnonymousUser

from poresira.core.models import GlobalRole, Project, ProjectRole
from poresira.core.permissions import can_edit_project
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory

pytestmark = pytest.mark.django_db


def member_with(role: ProjectRole, global_role=GlobalRole.TECHNICIAN):
    membership = ProjectMembershipFactory(role=role, user=UserFactory(role=global_role))
    return membership.user, membership.project


@pytest.mark.parametrize(
    ("project_role", "expected"),
    [
        (ProjectRole.LEAD, True),
        (ProjectRole.CONTRIBUTOR, True),
        (ProjectRole.VIEWER, False),
    ],
)
def test_project_role_decides_editing(project_role, expected):
    user, project = member_with(project_role)

    assert can_edit_project(user, project) is expected
    assert Project.objects.editable_by(user).filter(pk=project.pk).exists() is expected


@pytest.mark.parametrize(
    "global_role",
    [
        GlobalRole.PLATFORM_MANAGER,
        GlobalRole.TECHNICIAN,
        GlobalRole.BIOINFORMATICIAN,
        GlobalRole.PROJECT_LEAD,
    ],
)
def test_working_roles_edit_through_their_membership(global_role):
    user, project = member_with(ProjectRole.CONTRIBUTOR, global_role)

    assert can_edit_project(user, project)


def test_guest_never_edits_even_as_contributor():
    user, project = member_with(ProjectRole.LEAD, GlobalRole.GUEST)

    assert Project.objects.visible_to(user).filter(pk=project.pk).exists()
    assert not can_edit_project(user, project)


def test_member_cannot_edit_other_projects():
    user, _ = member_with(ProjectRole.LEAD)

    assert not can_edit_project(user, ProjectFactory())


@pytest.mark.parametrize(
    "make_user",
    [
        lambda: UserFactory(role=GlobalRole.ADMINISTRATOR),
        lambda: UserFactory(role=GlobalRole.GUEST, is_superuser=True),
    ],
)
def test_administrators_edit_every_project(make_user):
    assert can_edit_project(make_user(), ProjectFactory())


def test_anonymous_user_cannot_edit():
    assert not can_edit_project(AnonymousUser(), ProjectFactory())


def test_voided_membership_grants_no_edit_right():
    user, project = member_with(ProjectRole.LEAD)
    project.memberships.get().void(user=UserFactory(), reason="Left the project")

    assert not can_edit_project(user, project)


def test_nobody_edits_a_voided_project():
    user, project = member_with(ProjectRole.LEAD)
    admin = UserFactory(role=GlobalRole.ADMINISTRATOR)
    project.void(user=admin, reason="Duplicate")

    assert not can_edit_project(user, project)
    assert not can_edit_project(admin, project)
