# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from poresira.core.models import GlobalRole, ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.deposits.permissions import can_publish

pytestmark = pytest.mark.django_db


def member(project, role, global_role=GlobalRole.TECHNICIAN):
    user = UserFactory(role=global_role)
    ProjectMembershipFactory(project=project, user=user, role=role)
    return user


def test_only_a_lead_or_an_administrator_may_publish():
    project = ProjectFactory()

    assert can_publish(member(project, ProjectRole.LEAD), project)
    assert can_publish(UserFactory(role=GlobalRole.ADMINISTRATOR), project)
    assert not can_publish(member(project, ProjectRole.CONTRIBUTOR), project)
    assert not can_publish(member(project, ProjectRole.VIEWER), project)
    assert not can_publish(UserFactory(), project)


def test_a_guest_never_publishes_even_as_a_lead():
    project = ProjectFactory()

    assert not can_publish(member(project, ProjectRole.LEAD, GlobalRole.GUEST), project)


def test_a_lead_of_another_project_may_not_publish_here():
    assert not can_publish(member(ProjectFactory(), ProjectRole.LEAD), ProjectFactory())
