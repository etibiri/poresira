# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work for the Slurm agent: the Celery worker on the ``slurm`` queue, where ``sbatch`` exists."""

import logging

from celery import shared_task
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError

from poresira.core.models import User

from . import services
from .models import ComputeJob
from .scheduler import SchedulerError, scheduler_from_settings

logger = logging.getLogger(__name__)


def _poll_later(job_id: str) -> None:
    if not settings.CELERY_TASK_ALWAYS_EAGER:
        poll_job_task.apply_async(args=[job_id], countdown=settings.COMPUTING["POLL_SECONDS"])


@shared_task
def submit_job_task(job_id: str, user_id: str) -> str:
    """Submit a draft job. A refusal is kept in the notes of the job, which stays a draft."""
    job = ComputeJob.objects.get(pk=job_id)
    try:
        submitted = services.submit_job(
            user=User.objects.get(pk=user_id), job=job, scheduler=scheduler_from_settings()
        )
    except (SchedulerError, OSError, ValidationError, PermissionDenied) as error:
        services.note_problem(job, f"Submission failed: {error}")
        return "failed"
    except services.ComputeServiceError as error:
        return str(error)
    _poll_later(str(submitted.pk))
    return submitted.scheduler_job_id


@shared_task
def poll_job_task(job_id: str) -> str:
    """Ask the scheduler how the job is doing, then ask again later until it is over."""
    job = ComputeJob.objects.get(pk=job_id)
    try:
        job = services.refresh_job(job=job, scheduler=scheduler_from_settings())
    except SchedulerError as error:
        logger.warning("Could not read the state of job %s: %s", job.identifier, error)
    if not job.is_final:
        _poll_later(job_id)
    return job.status


@shared_task
def cancel_job_task(job_id: str, user_id: str) -> str:
    job = ComputeJob.objects.get(pk=job_id)
    try:
        cancelled = services.cancel_job(
            user=User.objects.get(pk=user_id), job=job, scheduler=scheduler_from_settings()
        )
    except (SchedulerError, services.ComputeServiceError, PermissionDenied) as error:
        services.note_problem(job, f"Cancellation failed: {error}")
        return "failed"
    return cancelled.status
