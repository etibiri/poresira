# SPDX-License-Identifier: AGPL-3.0-or-later
from django.db import migrations

from poresira.core.audit_migrations import attach_audit_chain, restrict_audit_tables

EVENT_TABLES = (
    "computing_computejobevent",
    "computing_jobinputevent",
    "computing_joboutputevent",
)


class Migration(migrations.Migration):
    dependencies = [("computing", "0001_initial")]

    operations = [
        *(attach_audit_chain(table) for table in EVENT_TABLES),
        restrict_audit_tables(*EVENT_TABLES),
    ]
