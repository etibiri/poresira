# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from poresira.core.audit import verify_audit_chain
from poresira.core.tests.test_audit import application_role, execute_expecting_denial
from poresira.storage.tests.factories import DataFileFactory

pytestmark = pytest.mark.django_db

AUDIT_TABLES = [
    ("storage_storagebackendevent", "name"),
    ("storage_datafileevent", "notes"),
    ("storage_fixitycheckevent", "notes"),
]


@pytest.mark.parametrize(("table", "column"), AUDIT_TABLES)
def test_application_role_cannot_rewrite_the_storage_audit_tables(table, column):
    with application_role():
        execute_expecting_denial(f"UPDATE {table} SET {column} = {column}")
        execute_expecting_denial(f"DELETE FROM {table}")
        execute_expecting_denial(f"TRUNCATE {table}")


def test_application_role_can_register_a_file_with_a_full_audit_trail():
    with application_role():
        file = DataFileFactory()

    assert verify_audit_chain().is_intact
    assert file.events.filter(pgh_label="insert").count() == 1
