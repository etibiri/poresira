# SPDX-License-Identifier: AGPL-3.0-or-later
from django.db import migrations

from poresira.core.audit_migrations import (
    attach_audit_chain,
    create_chain_functions,
    restrict_audit_tables,
)

EVENT_TABLES = ("core_projectevent", "core_projectmembershipevent")


class Migration(migrations.Migration):
    dependencies = [("core", "0004_audit_events")]

    operations = [
        create_chain_functions(),
        *(attach_audit_chain(table) for table in EVENT_TABLES),
        restrict_audit_tables(*EVENT_TABLES),
    ]
