# SPDX-License-Identifier: AGPL-3.0-or-later
from celery import shared_task

from poresira.core.models import User

from . import services
from .models import DataFile


@shared_task
def verify_file_task(file_id: str, user_id: str | None = None) -> str:
    """Check one file against its recorded checksums; hashing a large file can take minutes.

    The worker must see the backend root at the same path as the web server does.
    """
    file = DataFile.objects.select_related("backend", "project").get(pk=file_id)
    user = User.objects.get(pk=user_id) if user_id else None
    return services.verify_file(file=file, user=user).outcome
