# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from poresira.core.audit import verify_audit_chain
from poresira.core.tests.test_audit import application_role, execute_expecting_denial
from poresira.pcr.tests.factories import PcrReactionFactory

pytestmark = pytest.mark.django_db

AUDIT_TABLES = [
    ("pcr_primerevent", "name"),
    ("pcr_cyclingprogramevent", "name"),
    ("pcr_cyclingstepevent", "label"),
    ("pcr_mastermixevent", "name"),
    ("pcr_mastermixcomponentevent", "name"),
    ("pcr_assayevent", "name"),
    ("pcr_pcrrunevent", "notes"),
    ("pcr_pcrreactionevent", "notes"),
]


@pytest.mark.parametrize(("table", "column"), AUDIT_TABLES)
def test_application_role_cannot_rewrite_the_pcr_audit_tables(table, column):
    with application_role():
        execute_expecting_denial(f"UPDATE {table} SET {column} = {column}")
        execute_expecting_denial(f"DELETE FROM {table}")
        execute_expecting_denial(f"TRUNCATE {table}")


def test_application_role_can_record_a_run_with_a_full_audit_trail():
    with application_role():
        reaction = PcrReactionFactory()

    assert verify_audit_chain().is_intact
    assert reaction.events.filter(pgh_label="insert").count() == 1
