# SPDX-License-Identifier: AGPL-3.0-or-later
import factory

from poresira.core.models import GlobalRole, Project, ProjectMembership, ProjectRole, User


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.Sequence(lambda number: f"user{number}")
    role = GlobalRole.TECHNICIAN


class ProjectFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Project

    code = factory.Sequence(lambda number: f"proj-{number}")
    name = factory.Sequence(lambda number: f"Project {number}")


class ProjectMembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProjectMembership

    project = factory.SubFactory(ProjectFactory)
    user = factory.SubFactory(UserFactory)
    role = ProjectRole.CONTRIBUTOR
