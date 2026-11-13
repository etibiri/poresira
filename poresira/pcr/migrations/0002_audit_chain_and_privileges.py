# SPDX-License-Identifier: AGPL-3.0-or-later
from django.db import migrations

from poresira.core.audit_migrations import attach_audit_chain, restrict_audit_tables

EVENT_TABLES = (
    "pcr_primerevent",
    "pcr_cyclingprogramevent",
    "pcr_cyclingstepevent",
    "pcr_mastermixevent",
    "pcr_mastermixcomponentevent",
    "pcr_assayevent",
    "pcr_pcrrunevent",
    "pcr_pcrreactionevent",
)


class Migration(migrations.Migration):
    dependencies = [("pcr", "0001_initial")]

    operations = [
        *(attach_audit_chain(table) for table in EVENT_TABLES),
        restrict_audit_tables(*EVENT_TABLES),
    ]
