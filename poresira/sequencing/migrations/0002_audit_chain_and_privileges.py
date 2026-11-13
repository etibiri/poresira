# SPDX-License-Identifier: AGPL-3.0-or-later
from django.db import migrations

from poresira.core.audit_migrations import attach_audit_chain, restrict_audit_tables

EVENT_TABLES = (
    "sequencing_librarykitevent",
    "sequencing_flowcelltypeevent",
    "sequencing_instrumentevent",
    "sequencing_libraryevent",
    "sequencing_librarymemberevent",
    "sequencing_flowcellevent",
    "sequencing_sequencingrunevent",
    "sequencing_barcoderesultevent",
)


class Migration(migrations.Migration):
    dependencies = [("sequencing", "0001_initial")]

    operations = [
        *(attach_audit_chain(table) for table in EVENT_TABLES),
        restrict_audit_tables(*EVENT_TABLES),
    ]
