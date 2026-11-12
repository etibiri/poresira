# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError

from poresira.core.models import GlobalRole, Project, User
from poresira.core.tests.factories import (
    ProjectFactory,
    ProjectMembershipFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_projects():
    return ProjectFactory(), ProjectFactory()


def test_member_sees_only_their_projects(two_projects):
    own_project, other_project = two_projects
    member = UserFactory()
    ProjectMembershipFactory(project=own_project, user=member)

    visible_projects = Project.objects.visible_to(member)

    assert list(visible_projects) == [own_project]
    assert other_project not in visible_projects


def test_user_without_membership_sees_nothing(two_projects):
    assert not Project.objects.visible_to(UserFactory()).exists()


def test_guest_member_sees_only_their_project(two_projects):
    own_project, _ = two_projects
    guest = UserFactory(role=GlobalRole.GUEST)
    ProjectMembershipFactory(project=own_project, user=guest)

    assert list(Project.objects.visible_to(guest)) == [own_project]


def test_membership_of_another_user_grants_nothing(two_projects):
    own_project, _ = two_projects
    ProjectMembershipFactory(project=own_project)

    assert not Project.objects.visible_to(UserFactory()).exists()


@pytest.mark.parametrize(
    "make_user",
    [
        lambda: UserFactory(role=GlobalRole.ADMINISTRATOR),
        lambda: UserFactory(is_superuser=True),
    ],
)
def test_administrators_see_every_project(two_projects, make_user):
    assert Project.objects.visible_to(make_user()).count() == 2


def test_anonymous_user_sees_nothing(two_projects):
    assert not Project.objects.visible_to(AnonymousUser()).exists()


def test_user_in_several_projects_sees_each_once(two_projects):
    member = UserFactory()
    for project in two_projects:
        ProjectMembershipFactory(project=project, user=member)

    assert Project.objects.visible_to(member).count() == 2


def test_membership_is_unique_per_user_and_project():
    membership = ProjectMembershipFactory()

    with pytest.raises(IntegrityError):
        ProjectMembershipFactory(project=membership.project, user=membership.user)


def test_new_user_defaults_to_guest_role():
    assert User().role == GlobalRole.GUEST


def test_primary_keys_are_uuid7():
    assert ProjectFactory().id.version == 7
    assert UserFactory().id.version == 7
