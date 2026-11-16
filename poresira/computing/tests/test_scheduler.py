# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Slurm client and the agent, run against small fake Slurm programs."""

import stat
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from config import celery_app
from poresira.computing import services, tasks
from poresira.computing.models import ComputeJob, JobKind, JobOutput, JobStatus
from poresira.computing.scheduler import (
    SchedulerError,
    SlurmScheduler,
    status_of,
)
from poresira.computing.tests.factories import ComputeJobFactory
from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.sequencing.tests.factories import SequencingRunFactory
from poresira.storage.models import FileKind
from poresira.storage.tests.factories import DataFileFactory, StorageBackendFactory

pytestmark = pytest.mark.django_db

FAKE_SBATCH = """#!/bin/bash
[ "$1" = "--parsable" ] || exit 2
cp "$2" "{dir}/submitted.sh"
[ -f "{dir}/refuse" ] && { echo "sbatch: error: invalid partition" >&2; exit 1; }
echo "4821;cluster1"
"""
FAKE_SQUEUE = """#!/bin/bash
state=$(cat "{dir}/state" 2>/dev/null)
case "$state" in PENDING|RUNNING) echo "$state";; esac
"""
FAKE_SACCT = """#!/bin/bash
state=$(cat "{dir}/state" 2>/dev/null)
case "$state" in
  PENDING|RUNNING|"") ;;
  *) when="2026-11-16T10:00:00|2026-11-16T10:30:00"
     echo "4821|$state|$(cat "{dir}/exit" 2>/dev/null || echo 0:0)|$when"
     echo "4821.batch|$state|0:0|$when";;
esac
"""
FAKE_SCANCEL = """#!/bin/bash
echo "$1" > "{dir}/cancelled"
"""


class FakeSlurm:
    def __init__(self, directory: Path):
        self.directory = directory
        for name, body in (
            ("sbatch", FAKE_SBATCH),
            ("squeue", FAKE_SQUEUE),
            ("sacct", FAKE_SACCT),
            ("scancel", FAKE_SCANCEL),
        ):
            program = directory / name
            program.write_text(body.replace("{dir}", str(directory)))
            program.chmod(program.stat().st_mode | stat.S_IEXEC)

    def set_state(self, state: str, exit_code: str = "0:0"):
        (self.directory / "state").write_text(state)
        (self.directory / "exit").write_text(exit_code)

    @property
    def scheduler(self) -> SlurmScheduler:
        return SlurmScheduler(str(self.directory))


@pytest.fixture
def slurm(tmp_path):
    directory = tmp_path / "bin"
    directory.mkdir()
    return FakeSlurm(directory)


@pytest.fixture
def project():
    return ProjectFactory()


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


@pytest.fixture
def storage(tmp_path, settings):
    """A backend whose root holds the data and the results, as on the shared disk."""
    root = tmp_path / "data"
    (root / "computing").mkdir(parents=True)
    settings.COMPUTING = {
        **settings.COMPUTING,
        "OUTPUT_DIR": str(root / "computing"),
        "SLURM_BIN_DIR": str(tmp_path / "bin"),
    }
    return StorageBackendFactory(name="Shared disk", root=str(root))


@pytest.fixture
def draft(project, storage, editor):
    run = SequencingRunFactory(library__project=project)
    DataFileFactory(
        project=project,
        sequencing_run=run,
        kind=FileKind.POD5,
        path="runs/x.pod5",
        backend=storage,
    )
    return services.prepare_job(user=editor, run=run, kind=JobKind.BASECALLING)


class TestStates:
    @pytest.mark.parametrize(
        ("slurm_state", "status"),
        [
            ("PENDING", JobStatus.SUBMITTED),
            ("CONFIGURING", JobStatus.SUBMITTED),
            ("RUNNING", JobStatus.RUNNING),
            ("COMPLETING", JobStatus.RUNNING),
            ("COMPLETED", JobStatus.SUCCEEDED),
            ("FAILED", JobStatus.FAILED),
            ("TIMEOUT", JobStatus.FAILED),
            ("OUT_OF_MEMORY", JobStatus.FAILED),
            ("NODE_FAIL", JobStatus.FAILED),
            ("CANCELLED by 1000", JobStatus.CANCELLED),
            ("CANCELLED+", JobStatus.CANCELLED),
        ],
    )
    def test_slurm_states_map_to_job_statuses(self, slurm_state, status):
        assert status_of(slurm_state) == status


class TestClient:
    def test_a_script_is_submitted_and_the_number_read(self, slurm, tmp_path):
        script = tmp_path / "job.sh"
        script.write_text("#!/bin/bash\n")

        assert slurm.scheduler.submit(script) == "4821"
        assert (slurm.directory / "submitted.sh").read_text() == "#!/bin/bash\n"

    def test_a_refusal_is_reported_with_the_message_of_slurm(self, slurm, tmp_path):
        (slurm.directory / "refuse").write_text("")
        script = tmp_path / "job.sh"
        script.write_text("x")

        with pytest.raises(SchedulerError, match="invalid partition"):
            slurm.scheduler.submit(script)

    def test_a_missing_sbatch_is_a_scheduler_error(self, tmp_path):
        with pytest.raises(SchedulerError, match="could not be run"):
            SlurmScheduler(str(tmp_path / "nowhere")).submit(tmp_path / "x")

    def test_the_state_comes_from_the_queue_then_from_the_accounting(self, slurm):
        slurm.set_state("RUNNING")
        assert slurm.scheduler.state("4821").status == JobStatus.RUNNING

        slurm.set_state("COMPLETED")
        state = slurm.scheduler.state("4821")
        assert (state.status, state.exit_code) == (JobStatus.SUCCEEDED, 0)
        assert state.started_at and state.finished_at > state.started_at

        slurm.set_state("FAILED", exit_code="137:0")
        assert slurm.scheduler.state("4821").exit_code == 137

    def test_a_job_slurm_does_not_know_has_no_status(self, slurm):
        assert slurm.scheduler.state("4821").status is None

    @pytest.mark.parametrize("number", ["", "12; rm -rf /", "$(id)", "12 13", "abc"])
    def test_a_job_number_that_is_not_a_number_is_never_passed_on(self, slurm, number):
        with pytest.raises(SchedulerError, match="Not a Slurm job number"):
            slurm.scheduler.state(number)
        with pytest.raises(SchedulerError, match="Not a Slurm job number"):
            slurm.scheduler.cancel(number)

    def test_a_job_is_cancelled(self, slurm):
        slurm.scheduler.cancel("4821")

        assert (slurm.directory / "cancelled").read_text().strip() == "4821"


class TestSubmitting:
    def test_the_agent_creates_the_working_directory_and_submits_the_script(
        self, slurm, storage, editor, draft
    ):
        job = services.submit_job(user=editor, job=draft, scheduler=slurm.scheduler)

        work = Path(storage.root) / "computing" / job.identifier
        assert work.is_dir() and (work / "job.sh").read_text() == job.script
        assert (slurm.directory / "submitted.sh").read_text() == job.script
        assert job.status == JobStatus.SUBMITTED and job.scheduler_job_id == "4821"
        assert job.working_directory == str(work) and job.log_path == f"{work}/slurm-4821.log"
        assert job.submitted_by == editor and job.submitted_at is not None

    def test_a_job_is_submitted_once(self, slurm, editor, draft):
        services.submit_job(user=editor, job=draft, scheduler=slurm.scheduler)

        with pytest.raises(services.ComputeServiceError, match="draft"):
            services.submit_job(user=editor, job=draft, scheduler=slurm.scheduler)

    def test_a_refusal_leaves_the_job_a_draft(self, slurm, editor, draft):
        (slurm.directory / "refuse").write_text("")

        with pytest.raises(SchedulerError):
            services.submit_job(user=editor, job=draft, scheduler=slurm.scheduler)

        draft.refresh_from_db()
        assert draft.status == JobStatus.DRAFT and draft.scheduler_job_id == ""

    def test_readers_cannot_submit(self, slurm, project, draft):
        viewer = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user

        with pytest.raises(services.PermissionDenied):
            services.submit_job(user=viewer, job=draft, scheduler=slurm.scheduler)


class TestFollowing:
    @pytest.fixture
    def submitted(self, slurm, editor, draft):
        return services.submit_job(user=editor, job=draft, scheduler=slurm.scheduler)

    def test_a_queued_job_stays_submitted_then_runs(self, slurm, submitted):
        slurm.set_state("PENDING")
        assert services.refresh_job(job=submitted, scheduler=slurm.scheduler).status == "submitted"

        slurm.set_state("RUNNING")
        job = services.refresh_job(job=submitted, scheduler=slurm.scheduler)
        assert job.status == JobStatus.RUNNING and job.started_at is not None

    def test_a_job_the_scheduler_does_not_know_is_left_alone(self, slurm, submitted):
        job = services.refresh_job(job=submitted, scheduler=slurm.scheduler)

        assert job.status == JobStatus.SUBMITTED

    def test_a_failed_job_keeps_its_exit_code_and_registers_nothing(
        self, slurm, submitted, storage
    ):
        work = Path(submitted.working_directory)
        (work / "output").mkdir()
        (work / "output" / "partial.bam").write_bytes(b"x")
        slurm.set_state("FAILED", exit_code="1:0")

        job = services.refresh_job(job=submitted, scheduler=slurm.scheduler)

        assert (job.status, job.exit_code) == (JobStatus.FAILED, 1)
        assert job.finished_at is not None and not JobOutput.objects.exists()

    def test_a_job_that_succeeded_gets_its_results_registered_and_linked(
        self, slurm, submitted, storage
    ):
        output = Path(submitted.working_directory) / "output"
        output.mkdir()
        (output / "calls.bam").write_bytes(b"BAM" * 100)
        (output / "summary.txt").write_bytes(b"ok")
        slurm.set_state("COMPLETED")

        job = services.refresh_job(job=submitted, scheduler=slurm.scheduler)

        assert job.status == JobStatus.SUCCEEDED and job.exit_code == 0
        files = {row.file.path: row.file for row in job.outputs.all()}
        assert set(files) == {
            f"computing/{job.identifier}/output/calls.bam",
            f"computing/{job.identifier}/output/summary.txt",
        }
        calls = files[f"computing/{job.identifier}/output/calls.bam"]
        assert calls.kind == FileKind.BAM and calls.sequencing_run == job.sequencing_run
        assert calls.sha256 and calls.registered_by == job.submitted_by

    def test_results_outside_every_backend_are_noted_not_lost(
        self, slurm, submitted, storage, settings
    ):
        storage.void(user=submitted.submitted_by, reason="Retired")
        slurm.set_state("COMPLETED")

        job = services.refresh_job(job=submitted, scheduler=slurm.scheduler)

        assert job.status == JobStatus.SUCCEEDED
        assert "no storage backend" in job.notes and not job.outputs.exists()

    def test_a_job_without_a_results_folder_succeeds_with_a_note(self, slurm, submitted, storage):
        slurm.set_state("COMPLETED")

        job = services.refresh_job(job=submitted, scheduler=slurm.scheduler)

        assert job.status == JobStatus.SUCCEEDED and "Results not registered" in job.notes

    def test_a_finished_job_is_not_asked_about_again(self, slurm, submitted, storage):
        slurm.set_state("FAILED")
        services.refresh_job(job=submitted, scheduler=slurm.scheduler)
        slurm.set_state("COMPLETED")

        job = services.refresh_job(job=submitted, scheduler=slurm.scheduler)

        assert job.status == JobStatus.FAILED


class TestCancelling:
    def test_a_draft_is_cancelled_without_the_scheduler(self, editor, draft):
        job = services.cancel_job(user=editor, job=draft)

        assert job.status == JobStatus.CANCELLED and job.finished_at is not None

    def test_a_queued_job_is_stopped_in_the_scheduler(self, slurm, editor, draft):
        submitted = services.submit_job(user=editor, job=draft, scheduler=slurm.scheduler)

        job = services.cancel_job(user=editor, job=submitted, scheduler=slurm.scheduler)

        assert job.status == JobStatus.CANCELLED
        assert (slurm.directory / "cancelled").read_text().strip() == "4821"

    def test_a_finished_job_cannot_be_cancelled(self, editor, project):
        job = ComputeJobFactory(project=project)
        services.cancel_job(user=editor, job=job)

        with pytest.raises(services.ComputeServiceError, match="finished"):
            services.cancel_job(user=editor, job=job)


class TestTasks:
    def test_the_agent_submits_then_follows_the_job_to_its_end(self, slurm, editor, draft, storage):
        slurm.set_state("RUNNING")

        assert tasks.submit_job_task(str(draft.pk), str(editor.pk)) == "4821"
        draft.refresh_from_db()
        assert draft.status == JobStatus.SUBMITTED

        assert tasks.poll_job_task(str(draft.pk)) == JobStatus.RUNNING
        (Path(draft.working_directory) / "output").mkdir()
        slurm.set_state("COMPLETED")
        assert tasks.poll_job_task(str(draft.pk)) == JobStatus.SUCCEEDED

    def test_a_refused_submission_is_written_in_the_notes(self, slurm, editor, draft):
        (slurm.directory / "refuse").write_text("")

        assert tasks.submit_job_task(str(draft.pk), str(editor.pk)) == "failed"

        draft.refresh_from_db()
        assert draft.status == JobStatus.DRAFT and "invalid partition" in draft.notes

    def test_the_agent_cancels(self, slurm, editor, draft):
        tasks.submit_job_task(str(draft.pk), str(editor.pk))

        assert tasks.cancel_job_task(str(draft.pk), str(editor.pk)) == JobStatus.CANCELLED

    def test_the_tasks_use_the_slurm_queue(self):
        router = celery_app.amqp.router
        for task in (tasks.submit_job_task, tasks.poll_job_task, tasks.cancel_job_task):
            assert router.route({}, task.name)["queue"].name == "slurm"


class TestRefreshCommand:
    def test_every_active_job_is_updated(self, slurm, editor, draft, storage):
        services.submit_job(user=editor, job=draft, scheduler=slurm.scheduler)
        slurm.set_state("RUNNING")
        out = StringIO()

        call_command("refresh_jobs", stdout=out)

        assert f"{draft.identifier} running" in out.getvalue()
        assert ComputeJob.objects.get(pk=draft.pk).status == JobStatus.RUNNING

    def test_an_unreachable_scheduler_fails_the_command(self, editor, draft, storage, settings):
        settings.COMPUTING = {**settings.COMPUTING, "SLURM_BIN_DIR": "/nowhere"}
        ComputeJob.objects.filter(pk=draft.pk).update(
            status=JobStatus.SUBMITTED,
            submitted_at="2026-01-01T00:00:00Z",
            script="#!/bin/bash\n",
            scheduler_job_id="9",
        )

        with pytest.raises(CommandError, match="could not be read"):
            call_command("refresh_jobs", stdout=StringIO(), stderr=StringIO())
