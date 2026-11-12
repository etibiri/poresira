# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pytest

from poresira.core.reason import acting_as
from poresira.core.tests.factories import ProjectFactory, UserFactory

pytestmark = pytest.mark.django_db


def recorded_user(project) -> str | None:
    return project.events.latest("pgh_id").pgh_context.metadata.get("user")


def test_the_actor_is_recorded_outside_a_request():
    user = UserFactory()
    with acting_as(user):
        project = ProjectFactory()

    assert recorded_user(project) == str(user.pk)


def test_the_actor_does_not_leak_after_the_block():
    with acting_as(UserFactory()):
        ProjectFactory()
    later_project = ProjectFactory()

    assert later_project.events.latest("pgh_id").pgh_context is None


def test_a_user_already_in_the_context_is_not_replaced():
    request_user, other_user = UserFactory(), UserFactory()
    with pghistory.context(user=str(request_user.pk)), acting_as(other_user):
        project = ProjectFactory()

    assert recorded_user(project) == str(request_user.pk)


def test_the_actor_is_added_to_an_existing_context_and_removed_afterwards():
    user = UserFactory()
    with pghistory.context(url="/somewhere/") as context:
        with acting_as(user):
            assert context.metadata["user"] == str(user.pk)

        assert "user" not in context.metadata
