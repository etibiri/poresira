# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest import mock

import pytest
from django.urls import reverse

from poresira.core.models import GlobalRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory

pytestmark = pytest.mark.django_db

PASSWORD = "correct horse battery staple"


@pytest.fixture
def member(client):
    user = UserFactory(password="")
    user.set_password(PASSWORD)
    user.save()
    client.force_login(user)
    return user


@pytest.fixture
def two_projects(member):
    own_project = ProjectFactory(code="own-1", name="Cassava survey")
    other_project = ProjectFactory(code="other-1", name="Secret project")
    ProjectMembershipFactory(project=own_project, user=member)
    return own_project, other_project


def test_anonymous_user_is_sent_to_login(client):
    response = client.get(reverse("project-list"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))


def test_login_and_logout(client):
    user = UserFactory(username="amina")
    user.set_password(PASSWORD)
    user.save()

    login = client.post(reverse("login"), {"username": "amina", "password": PASSWORD})
    assert login.status_code == 302
    assert client.get(reverse("project-list")).status_code == 200

    assert client.post(reverse("logout")).status_code == 302
    assert client.get(reverse("project-list")).status_code == 302


def test_failed_login_shows_an_error(client):
    response = client.post(reverse("login"), {"username": "nobody", "password": "wrong"})

    assert response.status_code == 200
    assert response.context["form"].errors


def test_project_list_shows_only_member_projects(client, two_projects):
    response = client.get(reverse("project-list"))

    assert "Cassava survey" in response.text
    assert "Secret project" not in response.text


def test_administrator_sees_every_project(client, two_projects):
    admin = UserFactory(role=GlobalRole.ADMINISTRATOR)
    client.force_login(admin)

    response = client.get(reverse("project-list"))

    assert "Cassava survey" in response.text
    assert "Secret project" in response.text


def test_voided_projects_are_hidden_from_the_list(client, two_projects, member):
    own_project, _ = two_projects
    own_project.void(user=member, reason="Duplicate")

    assert "Cassava survey" not in client.get(reverse("project-list")).text


def test_htmx_filter_returns_only_matching_rows(client, member):
    for code, name in [("cas-1", "Cassava"), ("yam-1", "Yam")]:
        ProjectMembershipFactory(project=ProjectFactory(code=code, name=name), user=member)

    response = client.get(reverse("project-list"), {"q": "yam"}, HTTP_HX_REQUEST="true")

    assert "Yam" in response.text
    assert "Cassava" not in response.text
    assert "<html" not in response.text


def test_history_page_lists_changes_with_reason(client, two_projects, member):
    own_project, _ = two_projects
    own_project.name = "Cassava survey 2026"
    own_project.save()
    own_project.void(user=member, reason="Merged into another project")

    response = client.get(reverse("project-history", args=[own_project.pk]))

    assert response.status_code == 200
    assert "Merged into another project" in response.text
    assert "Cassava survey 2026" in response.text
    assert "Modified" in response.text


def test_history_of_a_foreign_project_is_not_found(client, two_projects):
    _, other_project = two_projects

    assert client.get(reverse("project-history", args=[other_project.pk])).status_code == 404


def test_footer_links_to_source_and_shows_version(client, settings):
    settings.PORESIRA_SOURCE_URL = "https://example.org/poresira"
    settings.PORESIRA_VERSION = "v1.2.3"

    page = client.get(reverse("login")).text

    assert 'href="https://example.org/poresira"' in page
    assert "v1.2.3" in page


def test_no_page_loads_assets_from_a_cdn(client, member):
    for page in (client.get(reverse("login")).text, client.get(reverse("project-list")).text):
        assert "http://" not in page.replace("http://www.w3.org", "")
        assert "cdn." not in page


def test_default_language_comes_from_settings(client, settings):
    assert "Log in" in client.get(reverse("login"), HTTP_ACCEPT_LANGUAGE="fr").text
    settings.LANGUAGE_CODE = "fr"

    page = client.get(reverse("login"), HTTP_ACCEPT_LANGUAGE="en").text

    assert "Se connecter" in page


def test_user_can_switch_language_and_it_is_remembered(client, member):
    response = client.post(reverse("set-language"), {"language": "fr", "next": "/"})
    member.refresh_from_db()

    assert response.status_code == 302
    assert member.language == "fr"
    assert "Projets" in client.get(reverse("project-list")).text


def test_language_switch_ignores_unknown_languages_and_foreign_redirects(client, member):
    response = client.post(
        reverse("set-language"), {"language": "xx", "next": "https://evil.example/"}
    )
    member.refresh_from_db()

    assert response.url == "/"
    assert member.language == ""


def test_anonymous_language_choice_is_kept_in_a_cookie(client):
    client.post(reverse("set-language"), {"language": "fr"})

    assert "Se connecter" in client.get(reverse("login")).text


def test_health_reports_ok(client):
    with mock.patch("poresira.core.views.redis.Redis.from_url") as redis_factory:
        redis_factory.return_value.ping.return_value = True
        response = client.get(reverse("health"))

    assert response.status_code == 200
    assert response.json() == {"database": "ok", "redis": "ok", "status": "ok"}


def test_health_reports_redis_failure(client):
    with mock.patch("poresira.core.views.redis.Redis.from_url", side_effect=OSError):
        response = client.get(reverse("health"))

    assert response.status_code == 503
    assert response.json() == {"database": "ok", "redis": "error", "status": "error"}


def test_health_reports_database_failure(client):
    with (
        mock.patch("poresira.core.views.connection.cursor", side_effect=OSError),
        mock.patch("poresira.core.views.redis.Redis.from_url") as redis_factory,
    ):
        redis_factory.return_value.ping.return_value = True
        response = client.get(reverse("health"))

    assert response.status_code == 503
    assert response.json()["database"] == "error"
