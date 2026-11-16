# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from poresira.computing.models import COMPUTE_JOB_TYPE_CODE, ComputeJob, JobStatus
from poresira.computing.tests.factories import (
    ComputeJobFactory,
    JobInputFactory,
    JobOutputFactory,
)
from poresira.core.audit import verify_audit_chain, with_reason
from poresira.core.identifiers import is_valid_identifier
from poresira.core.tests.factories import ProjectFactory
from poresira.core.voiding import HardDeleteForbiddenError
from poresira.sequencing.tests.factories import SequencingRunFactory
from poresira.storage.tests.factories import DataFileFactory

pytestmark = pytest.mark.django_db


def submitted_fields():
    return {"submitted_at": timezone.now(), "script": "#!/bin/bash\n"}


def refuses(error, action):
    with pytest.raises(error), transaction.atomic():
        action()


def running_job(**attributes):
    return ComputeJobFactory(status=JobStatus.RUNNING, **{**submitted_fields(), **attributes})


class TestJobs:
    def test_a_job_gets_a_valid_identifier_and_starts_as_a_draft(self):
        job = ComputeJobFactory()

        assert job.identifier.split("-")[1].startswith(COMPUTE_JOB_TYPE_CODE)
        assert is_valid_identifier(job.identifier)
        assert job.status == JobStatus.DRAFT and not job.is_final

    def test_a_job_acts_on_a_run_of_its_own_project(self):
        refuses(
            DatabaseError,
            lambda: ComputeJobFactory(
                project=ProjectFactory(), sequencing_run=SequencingRunFactory()
            ),
        )

    def test_what_identifies_a_job_cannot_change(self):
        job = ComputeJobFactory()

        for name, value in (("kind", "demultiplexing"), ("project", ProjectFactory())):
            setattr(job, name, value)
            refuses(DatabaseError, job.save)
            job.refresh_from_db()

    def test_a_submitted_job_needs_its_script_and_date(self):
        refuses(IntegrityError, lambda: ComputeJobFactory(status=JobStatus.SUBMITTED))
        refuses(
            IntegrityError,
            lambda: ComputeJobFactory(status=JobStatus.RUNNING, submitted_at=timezone.now()),
        )
        running_job()

    def test_a_finished_job_has_an_end_date(self):
        refuses(
            IntegrityError, lambda: running_job_with(status=JobStatus.SUCCEEDED, finished_at=None)
        )
        running_job_with(status=JobStatus.FAILED, finished_at=timezone.now())

    def test_a_draft_can_be_cancelled_without_a_script(self):
        job = ComputeJobFactory()

        job.status, job.finished_at = JobStatus.CANCELLED, timezone.now()
        job.save()

    def test_parameters_change_while_a_draft_and_never_after(self):
        job = ComputeJobFactory()
        job.parameters = {"model": "hac"}
        job.save()

        started = running_job()
        started.parameters = {"model": "fast"}
        refuses(DatabaseError, started.save)
        started.refresh_from_db()
        started.script = "#!/bin/bash\necho changed\n"
        refuses(DatabaseError, started.save)

    def test_a_finished_job_stays_finished(self):
        job = running_job()
        job.status, job.finished_at, job.exit_code = JobStatus.FAILED, timezone.now(), 1
        job.save()

        job.status = JobStatus.RUNNING
        refuses(DatabaseError, job.save)

    def test_the_progress_of_a_job_is_updated_without_a_reason(self):
        job = running_job()

        job.scheduler_job_id, job.started_at = "4821", timezone.now()
        job.save()

    def test_a_scheduler_job_id_is_unique_among_active_jobs(self):
        running_job(scheduler_job_id="4821")

        refuses(IntegrityError, lambda: running_job(scheduler_job_id="4821"))
        running_job(scheduler_job_id="")
        running_job(scheduler_job_id="")

    def test_a_job_is_voided_never_deleted(self):
        job = ComputeJobFactory()

        with pytest.raises(HardDeleteForbiddenError):
            ComputeJob.objects.filter(pk=job.pk).delete()
        refuses(DatabaseError, lambda: ComputeJob.objects.filter(pk=job.pk)._raw_delete("default"))


def running_job_with(**attributes):
    return ComputeJobFactory(**{**submitted_fields(), **attributes})


class TestInputsAndOutputs:
    def test_inputs_are_chosen_while_the_job_is_a_draft(self):
        job = ComputeJobFactory()
        row = JobInputFactory(job=job)

        assert row.sha256 == row.file.sha256
        job.status = JobStatus.SUBMITTED
        job.submitted_at, job.script = timezone.now(), "#!/bin/bash\n"
        job.save()
        refuses(DatabaseError, lambda: JobInputFactory(job=job))

    def test_an_input_belongs_to_the_project_of_the_job(self):
        job = ComputeJobFactory()

        refuses(
            DatabaseError, lambda: JobInputFactory(job=job, file=DataFileFactory(), sha256="a" * 64)
        )

    def test_a_file_is_an_input_once_and_its_checksum_is_well_formed(self):
        row = JobInputFactory()

        refuses(IntegrityError, lambda: JobInputFactory(job=row.job, file=row.file))
        refuses(IntegrityError, lambda: JobInputFactory(job=row.job, sha256="xyz"))

    def test_an_input_cannot_change(self):
        row = JobInputFactory()

        row.sha256 = "b" * 64
        refuses(DatabaseError, row.save)

    def test_outputs_come_from_a_running_or_succeeded_job_of_the_same_project(self):
        job = running_job()
        JobOutputFactory(job=job)
        job.status, job.finished_at, job.exit_code = JobStatus.SUCCEEDED, timezone.now(), 0
        job.save()
        JobOutputFactory(job=job)

        refuses(DatabaseError, lambda: JobOutputFactory(job=ComputeJobFactory()))
        refuses(DatabaseError, lambda: JobOutputFactory(job=job, file=DataFileFactory()))

    def test_a_failed_job_produces_nothing_more(self):
        job = running_job()
        job.status, job.finished_at, job.exit_code = JobStatus.FAILED, timezone.now(), 1
        job.save()

        refuses(DatabaseError, lambda: JobOutputFactory(job=job))

    def test_a_file_is_an_output_of_a_job_once(self):
        row = JobOutputFactory()

        refuses(IntegrityError, lambda: JobOutputFactory(job=row.job, file=row.file))


def test_changes_are_audited_and_the_chain_is_intact():
    job = running_job()
    JobOutputFactory(job=job)
    job.scheduler_job_id = "77"
    job.save()
    with with_reason("Not needed"):
        job.status, job.finished_at = JobStatus.CANCELLED, timezone.now()
        job.save()

    assert verify_audit_chain().is_intact
    assert job.events.filter(pgh_label="insert").count() == 1
