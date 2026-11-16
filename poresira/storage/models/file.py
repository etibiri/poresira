# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from poresira.core.models import HumanIdentifiedModel, Project, UUIDPrimaryKeyModel
from poresira.core.pgtriggers import RequireReason
from poresira.core.tracking import tracked
from poresira.core.voiding import VoidableModel, VoidableQuerySet
from poresira.pcr.models import PcrRun
from poresira.samples.models import Sample
from poresira.sequencing.models import SequencingRun

from .backend import StorageBackend

DATA_FILE_TYPE_CODE = "F"
LINKS = ("sequencing_run", "pcr_run", "sample")


class FileKind(models.TextChoices):
    POD5 = "pod5", _("POD5 (raw signal)")
    FAST5 = "fast5", _("FAST5 (raw signal)")
    FASTQ = "fastq", _("FASTQ (reads)")
    BAM = "bam", _("BAM (reads)")
    REPORT = "report", _("Run report")
    GEL_IMAGE = "gel_image", _("Gel image")
    OTHER = "other", _("Other")


class Tier(models.TextChoices):
    HOT = "hot", _("Hot")
    WARM = "warm", _("Warm")
    COLD = "cold", _("Cold")


class Integrity(models.TextChoices):
    UNCHECKED = "unchecked", _("Not checked")
    INTACT = "intact", _("Intact")
    MISMATCH = "mismatch", _("Changed")
    MISSING = "missing", _("Missing")
    UNREADABLE = "unreadable", _("Unreadable")


class DataFileQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "DataFileQuerySet":
        return self.filter(project__in=Project.objects.visible_to(user).values("pk"))


class FixityCheckQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "FixityCheckQuerySet":
        return self.filter(file__project__in=Project.objects.visible_to(user).values("pk"))


# A file is attached to one record, of the same project as the file.
FILE_RULES_SQL = """
    IF NEW.sequencing_run_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM sequencing_sequencingrun
        WHERE id = NEW.sequencing_run_id AND project_id = NEW.project_id
    ) THEN
        RAISE EXCEPTION 'A file belongs to the project of its sequencing run'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.pcr_run_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pcr_pcrrun WHERE id = NEW.pcr_run_id AND project_id = NEW.project_id
    ) THEN
        RAISE EXCEPTION 'A file belongs to the project of its PCR run'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.sample_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM samples_sample WHERE id = NEW.sample_id AND project_id = NEW.project_id
    ) THEN
        RAISE EXCEPTION 'A file belongs to the project of its sample'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""


@pghistory.track(*tracked())
class DataFile(UUIDPrimaryKeyModel, HumanIdentifiedModel, VoidableModel):
    """A file registered where it is: its place, size and checksums, never its content."""

    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="data_files", verbose_name=_("project")
    )
    backend = models.ForeignKey(
        StorageBackend, on_delete=models.PROTECT, related_name="files", verbose_name=_("backend")
    )
    path = models.CharField(
        _("path"),
        max_length=1000,
        help_text=_("Relative to the root of the backend, with no leading slash."),
    )
    kind = models.CharField(_("kind"), max_length=16, choices=FileKind.choices)
    size_bytes = models.PositiveBigIntegerField(_("size (bytes)"))
    sha256 = models.CharField(_("SHA-256"), max_length=64)
    md5 = models.CharField(_("MD5"), max_length=32)
    tier = models.CharField(_("tier"), max_length=8, choices=Tier.choices, default=Tier.HOT)
    integrity = models.CharField(
        _("integrity"), max_length=16, choices=Integrity.choices, default=Integrity.UNCHECKED
    )
    last_checked_at = models.DateTimeField(_("last checked"), null=True, blank=True)
    sequencing_run = models.ForeignKey(
        SequencingRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="files",
        verbose_name=_("sequencing run"),
    )
    pcr_run = models.ForeignKey(
        PcrRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="files",
        verbose_name=_("PCR run"),
    )
    sample = models.ForeignKey(
        Sample,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="files",
        verbose_name=_("sample"),
    )
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("registered by"),
    )
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DataFileQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["-created_at"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["backend", "path"],
                condition=models.Q(voided_at__isnull=True),
                name="one_active_file_per_place",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        sequencing_run__isnull=False, pcr_run__isnull=True, sample__isnull=True
                    )
                    | models.Q(
                        sequencing_run__isnull=True, pcr_run__isnull=False, sample__isnull=True
                    )
                    | models.Q(
                        sequencing_run__isnull=True, pcr_run__isnull=True, sample__isnull=False
                    )
                ),
                name="file_has_exactly_one_link",
            ),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"), name="file_sha256_format"
            ),
            models.CheckConstraint(
                condition=models.Q(md5__regex=r"^[0-9a-f]{32}$"), name="file_md5_format"
            ),
            models.CheckConstraint(
                condition=models.Q(path__regex=r"^[^/]") & ~models.Q(path__regex=r"(^|/)\.\.(/|$)"),
                name="file_path_is_relative",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_file_identity",
                fields=[
                    "identifier",
                    "project",
                    "backend",
                    "path",
                    "kind",
                    "size_bytes",
                    "sha256",
                    "md5",
                    "sequencing_run",
                    "pcr_run",
                    "sample",
                ],
            ),
            pgtrigger.Trigger(
                name="check_file_rules",
                when=pgtrigger.Before,
                operation=pgtrigger.Insert,
                func=pgtrigger.Func(FILE_RULES_SQL),
            ),
            RequireReason(name="require_reason_to_change_tier", fields=["tier"]),
        ]

    def identifier_type_code(self) -> str:
        return DATA_FILE_TYPE_CODE

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    def __str__(self) -> str:
        return f"{self.identifier} {self.name}"


@pghistory.track(*tracked())
class FixityCheck(UUIDPrimaryKeyModel, VoidableModel):
    """One check of a file against its recorded checksum. Checks are never edited."""

    file = models.ForeignKey(
        DataFile, on_delete=models.PROTECT, related_name="checks", verbose_name=_("file")
    )
    checked_at = models.DateTimeField(_("checked at"), default=timezone.now)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("checked by"),
    )
    outcome = models.CharField(_("outcome"), max_length=16, choices=Integrity.choices)
    observed_sha256 = models.CharField(_("observed SHA-256"), max_length=64, blank=True)
    observed_size_bytes = models.PositiveBigIntegerField(
        _("observed size (bytes)"), null=True, blank=True
    )
    notes = models.TextField(_("notes"), blank=True)

    objects = FixityCheckQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["-checked_at"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.CheckConstraint(
                condition=~models.Q(outcome=Integrity.UNCHECKED), name="check_has_an_outcome"
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_check",
                fields=[
                    "file",
                    "checked_at",
                    "outcome",
                    "observed_sha256",
                    "observed_size_bytes",
                ],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.file_id} {self.outcome}"
