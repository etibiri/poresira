# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from poresira.core.audit import verify_audit_chain
from poresira.core.tests.test_audit import application_role, execute_expecting_denial
from poresira.sequencing.tests.factories import SequencingRunFactory

pytestmark = pytest.mark.django_db

AUDIT_TABLES = [
    ("sequencing_librarykitevent", "name"),
    ("sequencing_flowcelltypeevent", "name"),
    ("sequencing_instrumentevent", "name"),
    ("sequencing_libraryevent", "name"),
    ("sequencing_librarymemberevent", "notes"),
    ("sequencing_flowcellevent", "notes"),
    ("sequencing_sequencingrunevent", "notes"),
    ("sequencing_barcoderesultevent", "notes"),
]


@pytest.mark.parametrize(("table", "column"), AUDIT_TABLES)
def test_application_role_cannot_rewrite_the_sequencing_audit_tables(table, column):
    with application_role():
        execute_expecting_denial(f"UPDATE {table} SET {column} = {column}")
        execute_expecting_denial(f"DELETE FROM {table}")
        execute_expecting_denial(f"TRUNCATE {table}")


def test_application_role_can_record_a_run_with_a_full_audit_trail():
    with application_role():
        run = SequencingRunFactory()

    assert verify_audit_chain().is_intact
    assert run.events.filter(pgh_label="insert").count() == 1
