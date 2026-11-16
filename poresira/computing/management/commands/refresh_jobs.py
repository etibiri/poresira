# SPDX-License-Identifier: AGPL-3.0-or-later
from django.core.management.base import BaseCommand, CommandError

from poresira.computing import services
from poresira.computing.models import ComputeJob, JobStatus
from poresira.computing.scheduler import SchedulerError, scheduler_from_settings


class Command(BaseCommand):
    help = (
        "Ask the scheduler about every submitted or running job and update them. Run it where "
        "sbatch exists, for example from cron, if the agent's own follow-up was interrupted."
    )

    def handle(self, *args, **options):
        scheduler = scheduler_from_settings()
        active = ComputeJob.objects.active().filter(
            status__in=[JobStatus.SUBMITTED, JobStatus.RUNNING]
        )
        failures = 0
        for job in active:
            try:
                refreshed = services.refresh_job(job=job, scheduler=scheduler)
            except SchedulerError as error:
                failures += 1
                self.stderr.write(f"{job.identifier}: {error}")
            else:
                self.stdout.write(f"{refreshed.identifier} {refreshed.status}")
        if failures:
            raise CommandError(f"{failures} job(s) could not be read.")
