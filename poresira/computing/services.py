# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every change to compute jobs goes through here: permissions, rules, reasons and audit.

A job is prepared as a draft with its inputs and validated parameters. Submitting it to the
scheduler is the work of the agent (``poresira.computing.tasks``).
"""

import re
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from poresira.core.permissions import can_edit_project
from poresira.core.reason import acting_as
from poresira.sequencing.models import SequencingRun
from poresira.storage.models import DataFile, Integrity

from .jobtypes import JOB_TYPES, Staged, Tool, clean_parameters
from .models import ComputeJob, JobInput, JobKind, JobOutput, JobStatus
from .scripts import InputFile, Resources, render_script

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
