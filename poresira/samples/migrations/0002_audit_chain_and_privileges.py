# SPDX-License-Identifier: AGPL-3.0-or-later
from django.db import migrations

from poresira.core.audit_migrations import attach_audit_chain, restrict_audit_tables

EVENT_TABLES = (
    "samples_storagelocationevent",
    "samples_collectioneventhistory",
    "samples_sampleevent",
    "samples_qcmeasurementevent",
)


class Migration(migrations.Migration):
    dependencies = [("samples", "0001_initial")]

    operations = [
        *(attach_audit_chain(table) for table in EVENT_TABLES),
        restrict_audit_tables(*EVENT_TABLES),
    ]
