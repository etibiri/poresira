# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from poresira.core.models import HumanIdentifiedModel, Project, UUIDPrimaryKeyModel
from poresira.core.tracking import tracked
from poresira.core.voiding import VoidableModel, VoidableQuerySet
from poresira.sequencing.models import SequencingRun
from poresira.storage.models import DataFile

COMPUTE_JOB_TYPE_CODE = "J"


class JobKind(models.TextChoices):
    BASECALLING = "basecalling", _("Basecalling")
    DEMULTIPLEXING = "demultiplexing", _("Demultiplexing")
    POD5_CONVERSION = "pod5_conversion", _("FAST5 to POD5 conversion")


class JobStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    SUBMITTED = "submitted", _("Submitted")
    RUNNING = "running", _("Running")
    SUCCEEDED = "succeeded", _("Succeeded")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


FINAL_STATUSES = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


class ComputeJobQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "ComputeJobQuerySet":
        return self.filter(project__in=Project.objects.visible_to(user).values("pk"))


class JobFileQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "JobFileQuerySet":
        return self.filter(job__project__in=Project.objects.visible_to(user).values("pk"))


# A job acts on a run of its own project.
JOB_RULES_SQL = """
    IF NOT EXISTS (
        SELECT 1 FROM sequencing_sequencingrun
        WHERE id = NEW.sequencing_run_id AND project_id = NEW.project_id
    ) THEN
        RAISE EXCEPTION 'A compute job belongs to the project of its sequencing run'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""

# Once a job has left the draft, what was asked and what was run no longer change; a finished
# job stays finished (run it again as a new job).
JOB_LIFECYCLE_SQL = """
    IF OLD.status <> 'draft' AND (
        OLD.parameters IS DISTINCT FROM NEW.parameters OR OLD.script IS DISTINCT FROM NEW.script
    ) THEN
        RAISE EXCEPTION 'A submitted job keeps its parameters and its script'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status IN ('succeeded', 'failed', 'cancelled') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'A finished job stays finished'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""

# Inputs are fixed while the job is a draft; outputs come while it runs or after it succeeded.
# Every file belongs to the project of the job.
INPUT_RULES_SQL = """
    IF NOT EXISTS (
        SELECT 1 FROM computing_computejob j JOIN storage_datafile f ON f.project_id = j.project_id
        WHERE j.id = NEW.job_id AND f.id = NEW.file_id
    ) THEN
        RAISE EXCEPTION 'An input file belongs to the project of the job'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM computing_computejob WHERE id = NEW.job_id AND status = 'draft'
    ) THEN
        RAISE EXCEPTION 'Inputs are chosen while the job is a draft'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""

OUTPUT_RULES_SQL = """
    IF NOT EXISTS (
        SELECT 1 FROM computing_computejob j JOIN storage_datafile f ON f.project_id = j.project_id
        WHERE j.id = NEW.job_id AND f.id = NEW.file_id
    ) THEN
        RAISE EXCEPTION 'An output file belongs to the project of the job'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM computing_computejob
        WHERE id = NEW.job_id AND status IN ('running', 'succeeded')
    ) THEN
        RAISE EXCEPTION 'Outputs come from a running or succeeded job'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""


@pghistory.track(*tracked())
class ComputeJob(UUIDPrimaryKeyModel, HumanIdentifiedModel, VoidableModel):
    """A calculation on the data of one sequencing run, run by a scheduler such as Slurm."""

    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="compute_jobs", verbose_name=_("project")
    )
    sequencing_run = models.ForeignKey(
        SequencingRun,
        on_delete=models.PROTECT,
        related_name="compute_jobs",
        verbose_name=_("sequencing run"),
    )
    kind = models.CharField(_("kind"), max_length=24, choices=JobKind.choices)
    status = models.CharField(
        _("status"), max_length=16, choices=JobStatus.choices, default=JobStatus.DRAFT
    )
    parameters = models.JSONField(_("parameters"), default=dict, blank=True)
    script = models.TextField(
        _("script"), blank=True, help_text=_("The batch script as it was submitted.")
    )
    tool = models.CharField(_("tool"), max_length=100, blank=True)
    tool_version = models.CharField(_("tool version"), max_length=50, blank=True)
    scheduler_job_id = models.CharField(_("scheduler job ID"), max_length=50, blank=True)
    working_directory = models.CharField(_("working directory"), max_length=1000, blank=True)
    log_path = models.CharField(_("log file"), max_length=1000, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("submitted by"),
    )
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)
    exit_code = models.SmallIntegerField(_("exit code"), null=True, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ComputeJobQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["-created_at"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["scheduler_job_id"],
                condition=models.Q(voided_at__isnull=True) & ~models.Q(scheduler_job_id=""),
                name="unique_active_scheduler_job_id",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["draft", "cancelled"])
                | models.Q(submitted_at__isnull=False) & ~models.Q(script=""),
                name="job_submitted_has_script_and_date",
            ),
            models.CheckConstraint(
                condition=~models.Q(status__in=["succeeded", "failed", "cancelled"])
                | models.Q(finished_at__isnull=False),
                name="job_finished_has_an_end_date",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_job_identity",
                fields=["identifier", "project", "sequencing_run", "kind"],
            ),
            pgtrigger.Trigger(
                name="check_job_rules",
                when=pgtrigger.Before,
                operation=pgtrigger.Insert,
                func=pgtrigger.Func(JOB_RULES_SQL),
            ),
            pgtrigger.Trigger(
                name="check_job_lifecycle",
                when=pgtrigger.Before,
                operation=pgtrigger.Update,
                func=pgtrigger.Func(JOB_LIFECYCLE_SQL),
            ),
        ]

    def identifier_type_code(self) -> str:
        return COMPUTE_JOB_TYPE_CODE

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES

    def __str__(self) -> str:
        return f"{self.identifier} {self.get_kind_display()}"


@pghistory.track(*tracked())
class JobInput(UUIDPrimaryKeyModel, VoidableModel):
    """A file the job reads, with the checksum it had when the job was prepared."""

    job = models.ForeignKey(
        ComputeJob, on_delete=models.PROTECT, related_name="inputs", verbose_name=_("job")
    )
    file = models.ForeignKey(
        DataFile, on_delete=models.PROTECT, related_name="job_inputs", verbose_name=_("file")
    )
    sha256 = models.CharField(_("SHA-256 at preparation"), max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = JobFileQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["job", "file"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["job", "file"],
                condition=models.Q(voided_at__isnull=True),
                name="one_active_input_per_file",
            ),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"), name="input_sha256_format"
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_input", fields=["job", "file", "sha256"]),
            pgtrigger.Trigger(
                name="check_input_rules",
                when=pgtrigger.Before,
                operation=pgtrigger.Insert,
                func=pgtrigger.Func(INPUT_RULES_SQL),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.job_id} {self.file_id}"


@pghistory.track(*tracked())
class JobOutput(UUIDPrimaryKeyModel, VoidableModel):
    """A file the job produced, registered where the job wrote it."""

    job = models.ForeignKey(
        ComputeJob, on_delete=models.PROTECT, related_name="outputs", verbose_name=_("job")
    )
    file = models.ForeignKey(
        DataFile, on_delete=models.PROTECT, related_name="job_outputs", verbose_name=_("file")
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = JobFileQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["job", "file"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["job", "file"],
                condition=models.Q(voided_at__isnull=True),
                name="one_active_output_per_file",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_output", fields=["job", "file"]),
            pgtrigger.Trigger(
                name="check_output_rules",
                when=pgtrigger.Before,
                operation=pgtrigger.Insert,
                func=pgtrigger.Func(OUTPUT_RULES_SQL),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.job_id} {self.file_id}"
