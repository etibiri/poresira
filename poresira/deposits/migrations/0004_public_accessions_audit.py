# SPDX-License-Identifier: AGPL-3.0-or-later
from django.db import migrations

from poresira.core.audit_migrations import attach_audit_chain, restrict_audit_tables

EVENT_TABLES = ("deposits_publicaccessionevent",)


class Migration(migrations.Migration):
    dependencies = [("deposits", "0003_public_accessions")]

    operations = [
        *(attach_audit_chain(table) for table in EVENT_TABLES),
        restrict_audit_tables(*EVENT_TABLES),
    ]
