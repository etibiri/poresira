# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work for the deposit agent: the Celery worker on the ``deposits`` queue, the only one that
reaches the archive. Give it the Webin account in its environment and nowhere else."""

from celery import shared_task
from django.core.exceptions import PermissionDenied, ValidationError

from poresira.core.models import User

from . import services
from .archive import ArchiveError
from .models import Deposit


@shared_task
def submit_deposit_task(deposit_id: str, user_id: str, confirm: str = "") -> str:
    """Submit a draft deposit. A refusal before anything is sent is kept in its notes and the
    deposit stays a draft."""
    deposit = Deposit.objects.get(pk=deposit_id)
    try:
        finished = services.submit_deposit(
            user=User.objects.get(pk=user_id), deposit=deposit, confirm=confirm
        )
    except (services.DepositServiceError, ValidationError, PermissionDenied, ArchiveError) as error:
        services.note_problem(deposit, f"Submission refused: {error}")
        return "refused"
    return finished.status
