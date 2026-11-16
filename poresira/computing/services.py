# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every change to compute jobs goes through here: permissions, rules, reasons and audit.

A job is prepared as a draft with its inputs and validated parameters. Submitting it to the
scheduler is the work of the agent (``poresira.computing.tasks``).
"""

import logging
import re
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from poresira.core.permissions import can_edit_project
from poresira.core.reason import acting_as
from poresira.sequencing.models import SequencingRun
from poresira.storage.importing import DirectoryError, register_directory
from poresira.storage.models import BackendKind, DataFile, Integrity, StorageBackend

from .jobtypes import JOB_TYPES, Staged, Tool, clean_parameters
from .models import ComputeJob, JobInput, JobKind, JobOutput, JobStatus
from .scripts import InputFile, Resources, render_script

logger = logging.getLogger(__name__)
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
USABLE_INTEGRITY = (Integrity.INTACT, Integrity.UNCHECKED)


class ComputeServiceError(Exception):
    """A rule of the computing module was not respected."""


class RecordInUseError(ComputeServiceError):
    """Raised when voiding a job whose results are still registered."""


def _require_editor(user, project) -> None:
    if not can_edit_project(user, project):
        raise PermissionDenied


def _save_new(instance) -> None:
    instance.full_clean(exclude=["identifier"])
    instance.save()


def default_parameters(run: SequencingRun, kind: str) -> dict:
    """What can be known from the run: the barcoding kit of its library."""
    kit = run.library.kit
    parameters = {}
    if kind == JobKind.DEMULTIPLEXING and kit.barcode_count:
        parameters["kit_name"] = kit.code
    if kind == JobKind.BASECALLING:
        parameters["model"] = "sup"
    return parameters


def default_inputs(run: SequencingRun, kind: str) -> list[DataFile]:
    """The files of the run this kind of job normally reads: its raw signal, or for
    demultiplexing the reads that basecalling produced."""
    job_type = JOB_TYPES[kind]
    if kind == JobKind.DEMULTIPLEXING:
        outputs = JobOutput.objects.active().filter(
            job__sequencing_run=run,
            job__kind=JobKind.BASECALLING,
            job__status=JobStatus.SUCCEEDED,
            file__voided_at__isnull=True,
            file__kind__in=job_type.input_kinds,
        )
        return [output.file for output in outputs.select_related("file__backend")]
    return list(run.files.active().filter(kind__in=job_type.input_kinds).select_related("backend"))


def _check_input(run: SequencingRun, kind: str, file: DataFile) -> None:
    allowed = JOB_TYPES[kind].input_kinds
    problem = None
    if file.is_voided:
        problem = _("%(file)s is voided.")
    elif file.project_id != run.project_id:
        problem = _("%(file)s belongs to another project.")
    elif file.kind not in allowed:
        problem = _("%(file)s is not a file this job reads.")
    elif not file.backend.is_readable:
        problem = _("%(file)s is on an offline medium.")
    elif file.integrity not in USABLE_INTEGRITY:
        problem = _("%(file)s has changed or is missing: check it first.")
    elif CONTROL_CHARACTERS.search(file.path):
        problem = _("%(file)s has a path that cannot be used in a script.")
    if problem:
        raise ValidationError(problem % {"file": file.name})


@transaction.atomic
def prepare_job(
    *,
    user,
    run: SequencingRun,
    kind: str,
    parameters: dict | None = None,
    inputs: list[DataFile] | None = None,
    notes: str = "",
) -> ComputeJob:
    """Create a draft job with its validated parameters and input files (by default, those of
    the run that this kind of job reads)."""
    _require_editor(user, run.project)
    if run.is_voided:
        raise ValidationError(_("A voided run cannot be processed."))
    if kind not in JOB_TYPES:
        raise ValidationError({"kind": _("Unknown kind of job.")})
    wanted = {**default_parameters(run, kind), **(parameters or {})}
    cleaned = clean_parameters(JOB_TYPES[kind], wanted)
    files = default_inputs(run, kind) if inputs is None else list(inputs)
    if not files:
        raise ValidationError(_("There is no input file for this job."))
    for file in files:
        _check_input(run, kind, file)
    job = ComputeJob(
        project=run.project,
        sequencing_run=run,
        kind=kind,
        parameters=cleaned,
        tool=JOB_TYPES[kind].tool,
        tool_version=settings.COMPUTING[f"{JOB_TYPES[kind].tool.upper()}_VERSION"],
        notes=notes,
    )
    with acting_as(user):
        _save_new(job)
        for file in files:
            _save_new(JobInput(job=job, file=file, sha256=file.sha256))
    return job


@transaction.atomic
def set_parameters(*, user, job: ComputeJob, parameters: dict) -> ComputeJob:
    """Change the parameters of a draft; later they are part of what was run."""
    _require_editor(user, job.project)
    if job.is_voided or job.status != JobStatus.DRAFT:
        raise ComputeServiceError(_("Only a draft job can be changed."))
    job.parameters = clean_parameters(JOB_TYPES[job.kind], parameters)
    with acting_as(user):
        job.full_clean()
        job.save()
    return job


def render_job_script(job: ComputeJob) -> str:
    """The batch script for this job as configured now. At submission it is stored in the job."""
    config = settings.COMPUTING
    job_type = JOB_TYPES[job.kind]
    inputs = [
        InputFile(path=str(Path(row.file.backend.root) / row.file.path), sha256=row.sha256)
        for row in job.inputs.active().select_related("file__backend").order_by("file__path")
    ]
    staged = Staged(
        parameters=job.parameters,
        dorado=Tool(tuple(config["DORADO"].split()), config["DORADO_VERSION"]),
        pod5=Tool(tuple(config["POD5"].split()), config["POD5_VERSION"]),
        models_directory=config["DORADO_MODELS_DIR"],
    )
    return render_script(
        identifier=job.identifier,
        job_type=job_type,
        staged=staged,
        inputs=inputs,
        work_directory=f"{config['OUTPUT_DIR'].rstrip('/')}/{job.identifier}",
        resources=Resources(
            gpus=config["GPUS"],
            cpus=config["CPUS"],
            mem_gb=config["MEM_GB"],
            time=config["TIME"],
            partition=config["SLURM_PARTITION"],
            account=config["SLURM_ACCOUNT"],
        ),
        setup=config["SETUP"],
    )


@transaction.atomic
def void_record(*, user, job: ComputeJob, reason: str) -> ComputeJob:
    """Void a draft or a finished job; a job in the queue must be cancelled first."""
    _require_editor(user, job.project)
    if job.status in (JobStatus.SUBMITTED, JobStatus.RUNNING):
        raise ComputeServiceError(_("Cancel the job before voiding it."))
    if job.outputs.active().exists():
        raise RecordInUseError(_("Void the results of this job first."))
    with acting_as(user):
        job.void(user=user, reason=reason)
    return job


# --- Running -----------------------------------------------------------------------------------


def _locked(job: ComputeJob) -> ComputeJob:
    """The job as stored, locked until the end of the transaction."""
    return ComputeJob.objects.select_for_update().select_related("project").get(pk=job.pk)


def _work_directory(job: ComputeJob) -> Path:
    return Path(settings.COMPUTING["OUTPUT_DIR"]) / job.identifier


@transaction.atomic
def submit_job(*, user, job: ComputeJob, scheduler) -> ComputeJob:
    """Write the script where the cluster can read it and hand it to the scheduler.

    The agent creates the working directory first: Slurm loses the log of a job whose output
    folder does not exist. The script is stored in the job as it was submitted.
    """
    job = _locked(job)
    _require_editor(user, job.project)
    if job.is_voided or job.status != JobStatus.DRAFT:
        raise ComputeServiceError(_("Only a draft job can be submitted."))
    script = render_job_script(job)
    work_directory = _work_directory(job)
    work_directory.mkdir(parents=True, exist_ok=True)
    script_path = work_directory / "job.sh"
    script_path.write_text(script)
    script_path.chmod(0o750)
    scheduler_job_id = scheduler.submit(script_path)
    job.status = JobStatus.SUBMITTED
    job.script = script
    job.scheduler_job_id = scheduler_job_id
    job.working_directory = str(work_directory)
    job.log_path = f"{work_directory}/slurm-{scheduler_job_id}.log"
    job.submitted_by = user
    job.submitted_at = timezone.now()
    with acting_as(user):
        job.full_clean()
        job.save()
    return job


def note_problem(job: ComputeJob, message: str) -> None:
    """Keep a problem in the notes of the job, outside any transaction that is being undone."""
    with transaction.atomic():
        job = _locked(job)
        job.notes = f"{job.notes}\n{message}".strip()
        job.save(update_fields=["notes", "updated_at"])


def _backend_covering(directory: Path) -> StorageBackend | None:
    """The readable backend whose root holds ``directory``; the deepest root wins."""
    directory = directory.resolve()
    roots = [
        (Path(backend.root).resolve(), backend)
        for backend in StorageBackend.objects.active().exclude(kind=BackendKind.OFFLINE)
    ]
    inside = [(root, backend) for root, backend in roots if directory.is_relative_to(root)]
    return max(inside, key=lambda pair: len(pair[0].parts))[1] if inside else None


def _register_outputs(job: ComputeJob) -> None:
    """Register what the job wrote under ``output/`` where it is, and link it to the job."""
    directory = Path(job.working_directory) / "output"
    backend = _backend_covering(directory)
    if backend is None:
        job.notes = f"{job.notes}\nResults not registered: no storage backend holds {directory}."
        job.save(update_fields=["notes", "updated_at"])
        return
    try:
        report = register_directory(
            user=job.submitted_by,
            project=job.project,
            backend=backend,
            directory=directory,
            link={"sequencing_run": job.sequencing_run},
        )
    except DirectoryError as error:
        job.notes = f"{job.notes}\nResults not registered: {error}"
        job.save(update_fields=["notes", "updated_at"])
        return
    with acting_as(job.submitted_by):
        for file in report.registered:
            _save_new(JobOutput(job=job, file=file))
    if report.failed:
        listed = "; ".join(f"{path} ({problem})" for path, problem in report.failed)
        job.notes = f"{job.notes}\nNot registered: {listed}"
        job.save(update_fields=["notes", "updated_at"])


@transaction.atomic
def refresh_job(*, job: ComputeJob, scheduler) -> ComputeJob:
    """Bring the job up to date with the scheduler. A job that succeeded gets its results
    registered. A job the scheduler does not know is left as it is."""
    job = _locked(job)
    if job.status not in (JobStatus.SUBMITTED, JobStatus.RUNNING):
        return job
    state = scheduler.state(job.scheduler_job_id)
    if state.status in (None, job.status):
        return job
    if state.status == JobStatus.SUBMITTED:  # back in the queue
        return job
    now = timezone.now()
    if state.status != JobStatus.RUNNING:
        job.finished_at = state.finished_at or now
        job.exit_code = state.exit_code
    if job.started_at is None and state.status != JobStatus.SUBMITTED:
        job.started_at = state.started_at or now
    job.status = state.status
    with acting_as(job.submitted_by):
        job.full_clean()
        job.save()
        if job.status == JobStatus.SUCCEEDED:
            _register_outputs(job)
    return job


@transaction.atomic
def cancel_job(*, user, job: ComputeJob, scheduler=None) -> ComputeJob:
    """Stop a job. A draft is cancelled in the database; a queued or running job is stopped in
    the scheduler (which needs ``scheduler``, so this runs on the agent)."""
    job = _locked(job)
    _require_editor(user, job.project)
    if job.is_voided or job.is_final:
        raise ComputeServiceError(_("This job is already finished."))
    if job.status != JobStatus.DRAFT:
        scheduler.cancel(job.scheduler_job_id)
    job.status = JobStatus.CANCELLED
    job.finished_at = timezone.now()
    with acting_as(user):
        job.full_clean()
        job.save()
    return job
