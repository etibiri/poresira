# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.db.models.signals import pre_migrate
from django.dispatch import receiver
from django.utils import translation


@receiver(pre_migrate)
def grant_default_privileges_to_app_role(sender, using, **kwargs):
    """Mirror docker/postgres/init-roles.sh on the test database, before migrations run.

    Without these default privileges the application role could touch nothing, and the
    audit-table REVOKE tests would pass for the wrong reason.
    """
    role = settings.DATABASE_APP_ROLE
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
        if cursor.fetchone() is None:
            return
        cursor.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{role}"'
        )
        cursor.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f'GRANT USAGE, SELECT ON SEQUENCES TO "{role}"'
        )


@pytest.fixture(scope="session", autouse=True)
def compiled_translations():
    """The .mo files are build artefacts; compile them so French strings can be tested."""
    call_command("compilemessages", verbosity=0)


@pytest.fixture(autouse=True)
def english_by_default():
    """A test that switches language (the language form, translation checks) must not leak it."""
    translation.activate(settings.LANGUAGE_CODE)
    yield
    translation.deactivate()
