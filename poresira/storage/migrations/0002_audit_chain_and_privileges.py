# SPDX-License-Identifier: AGPL-3.0-or-later
from django.db import migrations

from poresira.core.audit_migrations import attach_audit_chain, restrict_audit_tables

EVENT_TABLES = (
    "storage_storagebackendevent",
    "storage_datafileevent",
    "storage_fixitycheckevent",
)


class Migration(migrations.Migration):
    dependencies = [("storage", "0001_initial")]

    operations = [
        *(attach_audit_chain(table) for table in EVENT_TABLES),
        restrict_audit_tables(*EVENT_TABLES),
    ]
