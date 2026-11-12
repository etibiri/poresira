# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.db import connection

from poresira.core.audit import verify_audit_chain, with_reason
from poresira.core.tests.test_audit import application_role, execute_expecting_denial
from poresira.samples.models import SampleStatus
from poresira.samples.tests.factories import (
    CollectionEventFactory,
    DerivativeFactory,
    QcMeasurementFactory,
    SampleFactory,
    StorageLocationFactory,
)

pytestmark = pytest.mark.django_db

AUDIT_TABLES_AND_COLUMNS = [
    ("samples_sampleevent", "label"),
    ("samples_collectioneventhistory", "locality"),
    ("samples_storagelocationevent", "name"),
    ("samples_qcmeasurementevent", "notes"),
]


@pytest.mark.parametrize(("table", "column"), AUDIT_TABLES_AND_COLUMNS)
def test_application_role_cannot_rewrite_the_new_audit_tables(table, column):
    SampleFactory()
    with application_role():
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {table}")  # reading stays allowed
        execute_expecting_denial(f"UPDATE {table} SET {column} = {column}")
        execute_expecting_denial(f"DELETE FROM {table}")
        execute_expecting_denial(f"TRUNCATE {table}")


def test_application_role_can_run_the_whole_sample_workflow_with_a_full_audit_trail():
    with application_role():
        location = StorageLocationFactory(name="Box 1", kind="box", grid_rows=9, grid_columns=9)
        sample = SampleFactory(collection_event=CollectionEventFactory())
        extract = DerivativeFactory(parent=sample.parent or sample, project=sample.project)
        extract.storage_location, extract.position = location, "A1"
        with with_reason("Stored after extraction"):
            extract.save()
        extract.status = SampleStatus.STORED
        with with_reason("Stored after extraction"):
            extract.save()
        QcMeasurementFactory(sample=extract)

    assert verify_audit_chain().is_intact
    assert extract.events.filter(pgh_label="update").count() == 2
