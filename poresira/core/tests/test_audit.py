# SPDX-License-Identifier: AGPL-3.0-or-later
import threading
from contextlib import contextmanager
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, connections, transaction
from django.test import RequestFactory

from poresira.core.audit import verify_audit_chain
from poresira.core.middleware import AuditContextMiddleware
from poresira.core.models import AuditChainLink, Project, ProjectEvent
from poresira.core.tests.factories import ProjectFactory, UserFactory

pytestmark = pytest.mark.django_db


@contextmanager
def application_role():
    """Run statements with the privileges of the application role (no audit write access)."""
    with connection.cursor() as cursor:
        cursor.execute(f'SET ROLE "{settings.DATABASE_APP_ROLE}"')
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")


def execute_expecting_denial(sql: str):
    with (
        pytest.raises(DatabaseError, match="permission denied"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql)


def test_creating_and_editing_a_project_records_audit_entries():
    project = ProjectFactory(name="First name")
    project.name = "Second name"
    project.save()

    events = ProjectEvent.objects.filter(pgh_obj=project).order_by("pgh_id")

    assert [event.pgh_label for event in events] == ["insert", "update"]
    assert [event.name for event in events] == ["First name", "Second name"]


def test_saving_without_changes_adds_no_update_entry():
    project = ProjectFactory()
    project.save()

    assert ProjectEvent.objects.filter(pgh_obj=project).count() == 1


def test_request_context_is_attached_to_audit_entries():
    user = UserFactory()
    project = ProjectFactory()

    def edit_project(request):
        project.name = "Renamed in request"
        project.save()
        return type("Response", (dict,), {})()

    request = RequestFactory().post(
        "/projects/1/",
        {"reason": "Typo in the name"},
        REMOTE_ADDR="10.1.2.3",
        HTTP_X_REQUEST_ID="req-abc-12345",
    )
    request.user = user
    response = AuditContextMiddleware(edit_project)(request)

    update_event = ProjectEvent.objects.get(pgh_obj=project, pgh_label="update")
    metadata = update_event.pgh_context.metadata
    assert metadata["user"] == str(user.pk)
    assert metadata["ip_address"] == "10.1.2.3"
    assert metadata["request_id"] == "req-abc-12345"
    assert metadata["reason"] == "Typo in the name"
    assert metadata["url"] == "/projects/1/"
    assert response["X-Request-ID"] == "req-abc-12345"


def test_forwarded_ip_is_ignored_unless_trusted(settings):
    def get_response(request):
        return {}

    def recorded_ip() -> str:
        request = RequestFactory().post(
            "/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.9, 10.0.0.1"
        )
        request.user = UserFactory()
        AuditContextMiddleware(get_response)(request)
        return AuditContextMiddleware(get_response).get_context(request)["ip_address"]

    assert recorded_ip() == "10.0.0.1"
    settings.PORESIRA_TRUST_X_FORWARDED_FOR = True
    assert recorded_ip() == "203.0.113.9"


def test_invalid_request_id_is_replaced():
    request = RequestFactory().post("/", HTTP_X_REQUEST_ID="bad id; drop table")
    request.user = UserFactory()
    response = AuditContextMiddleware(lambda request: {})(request)

    assert response["X-Request-ID"] != "bad id; drop table"
    assert len(response["X-Request-ID"]) == 32


AUDIT_TABLES_AND_COLUMNS = [
    ("core_projectevent", "name"),
    ("core_projectmembershipevent", "role"),
    ("pghistory_context", "metadata"),
    ("core_auditchainlink", "row_hash"),
]


@pytest.mark.parametrize(("table", "column"), AUDIT_TABLES_AND_COLUMNS)
def test_application_role_cannot_update_or_delete_audit_tables(table, column):
    ProjectFactory()
    with application_role():
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {table}")  # reading stays allowed
        execute_expecting_denial(f"UPDATE {table} SET {column} = {column}")
        execute_expecting_denial(f"DELETE FROM {table}")
        execute_expecting_denial(f"TRUNCATE {table}")


def test_application_role_cannot_forge_chain_links_or_contexts():
    with application_role():
        execute_expecting_denial(
            "INSERT INTO core_auditchainlink (event_table, event_id, row_hash) VALUES ('x', 1, 'y')"
        )
        execute_expecting_denial(
            "INSERT INTO pghistory_context (id, created_at, updated_at, metadata) "
            "VALUES (gen_random_uuid(), now(), now(), '{}')"
        )


def test_application_role_can_still_write_domain_data_with_full_audit():
    with application_role():
        project = ProjectFactory(name="Written by the app role")
        project.name = "Edited by the app role"
        project.save()

    assert ProjectEvent.objects.filter(pgh_obj=project).count() == 2
    assert AuditChainLink.objects.count() == 2
    assert verify_audit_chain().is_intact


def test_untouched_chain_verifies():
    project = ProjectFactory()
    project.name = "Edited"
    project.save()

    report = verify_audit_chain()

    assert report.is_intact
    assert report.link_count == 2
    assert report.head_hash == AuditChainLink.objects.order_by("-seq").first().row_hash


def test_empty_chain_verifies():
    report = verify_audit_chain()

    assert report.is_intact
    assert report.link_count == 0


def make_three_links() -> list[AuditChainLink]:
    project = ProjectFactory()
    project.name = "Second"
    project.save()
    project.name = "Third"
    project.save()
    return list(AuditChainLink.objects.order_by("seq"))


def test_altered_event_row_is_detected():
    first_link, *_ = make_three_links()
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE core_projectevent SET name = 'tampered' WHERE pgh_id = %s",
            [first_link.event_id],
        )

    report = verify_audit_chain()

    assert [seq for seq, _ in report.problems] == [first_link.seq]
    assert "altered" in report.problems[0][1]


def test_deleted_event_row_is_detected():
    _, middle_link, _ = make_three_links()
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM core_projectevent WHERE pgh_id = %s", [middle_link.event_id])

    report = verify_audit_chain()

    assert (middle_link.seq, "audit event row is missing") in report.problems


def test_removed_chain_link_is_detected():
    _, middle_link, last_link = make_three_links()
    AuditChainLink.objects.filter(pk=middle_link.pk).delete()

    report = verify_audit_chain()

    assert [seq for seq, _ in report.problems if seq is not None] == [last_link.seq]
    assert any("no chain link" in problem for _, problem in report.problems)


def test_rewritten_hash_is_detected():
    first_link, *_ = make_three_links()
    AuditChainLink.objects.filter(pk=first_link.pk).update(row_hash="0" * 64)

    report = verify_audit_chain()

    assert not report.is_intact
    assert first_link.seq in [seq for seq, _ in report.problems]


def test_verify_command_reports_ok_and_failure():
    first_link, *_ = make_three_links()
    output = StringIO()
    call_command("verify_audit_chain", stdout=output)
    assert "Audit chain OK: 3 link(s)" in output.getvalue()

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE core_projectevent SET name = 'tampered' WHERE pgh_id = %s",
            [first_link.event_id],
        )
    errors = StringIO()
    with pytest.raises(CommandError, match="broken"):
        call_command("verify_audit_chain", stderr=errors)
    assert f"BREAK at link {first_link.seq}" in errors.getvalue()


@pytest.mark.django_db(transaction=True)
def test_concurrent_writers_keep_the_chain_intact():
    thread_count, projects_per_thread = 4, 10
    errors: list[Exception] = []
    start_together = threading.Barrier(thread_count)

    def write_projects():
        try:
            start_together.wait()
            for _ in range(projects_per_thread):
                ProjectFactory()
        except Exception as error:
            errors.append(error)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=write_projects) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    report = verify_audit_chain()
    assert not errors
    assert Project.objects.count() == thread_count * projects_per_thread
    assert report.link_count == thread_count * projects_per_thread
    assert report.is_intact
