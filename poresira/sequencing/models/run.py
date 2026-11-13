# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from poresira.core.models import HumanIdentifiedModel, Project, UUIDPrimaryKeyModel
from poresira.core.pgtriggers import RequireReason
from poresira.core.tracking import tracked
from poresira.core.voiding import VoidableModel, VoidableQuerySet
from poresira.samples.models import Sample

from .reference import FlowCellType, Instrument, LibraryKit

LIBRARY_TYPE_CODE = "B"
SEQUENCING_RUN_TYPE_CODE = "Q"
SEQUENCEABLE_KINDS = ("dna_extract", "rna_extract", "amplification_product")


class LibraryStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    SEALED = "sealed", _("Sealed")


class RunStatus(models.TextChoices):
    PLANNED = "planned", _("Planned")
    RUNNING = "running", _("Running")
    COMPLETED = "completed", _("Completed")
    ABORTED = "aborted", _("Aborted")


class LibraryQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "LibraryQuerySet":
        return self.filter(project__in=Project.objects.visible_to(user).values("pk"))


class LibraryMemberQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "LibraryMemberQuerySet":
        return self.filter(library__project__in=Project.objects.visible_to(user).values("pk"))


class SequencingRunQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "SequencingRunQuerySet":
        return self.filter(project__in=Project.objects.visible_to(user).values("pk"))


class BarcodeResultQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "BarcodeResultQuerySet":
        return self.filter(run__project__in=Project.objects.visible_to(user).values("pk"))


# What a member needs: the project of its library, a sequenceable kind, a barcode inside the kit's
# range (empty only when the library is reconstructed), and an open library.
MEMBER_RULES_SQL = """
    IF EXISTS (
        SELECT 1 FROM sequencing_library WHERE id = NEW.library_id AND status = 'sealed'
    ) THEN
        RAISE EXCEPTION 'A sealed library cannot gain or change members'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM samples_sample s JOIN sequencing_library l ON l.id = NEW.library_id
        WHERE s.id = NEW.sample_id AND s.project_id <> l.project_id
    ) THEN
        RAISE EXCEPTION 'The sample of a library member must belong to the project of the library'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM samples_sample
        WHERE id = NEW.sample_id AND kind IN ('dna_extract', 'rna_extract', 'amplification_product')
    ) THEN
        RAISE EXCEPTION 'Only DNA extracts, RNA extracts and amplification products are sequenced'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.barcode IS NULL AND NOT EXISTS (
        SELECT 1 FROM sequencing_library WHERE id = NEW.library_id AND reconstructed
    ) THEN
        RAISE EXCEPTION 'A barcode is required unless the library is reconstructed'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.barcode IS NOT NULL AND EXISTS (
        SELECT 1 FROM sequencing_library l JOIN sequencing_librarykit k ON k.id = l.kit_id
        WHERE l.id = NEW.library_id AND (k.barcode_count IS NULL OR NEW.barcode > k.barcode_count)
    ) THEN
        RAISE EXCEPTION 'The barcode is outside the range of the kit'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""

# A run uses a sealed library of its own project. A single-use flow cell (Flongle) has one run.
RUN_RULES_SQL = """
    IF NOT EXISTS (
        SELECT 1 FROM sequencing_library
        WHERE id = NEW.library_id AND project_id = NEW.project_id AND status = 'sealed'
    ) THEN
        RAISE EXCEPTION 'A run needs a sealed library of its own project'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM sequencing_flowcell f JOIN sequencing_flowcelltype t ON t.id = f.type_id
        WHERE f.id = NEW.flow_cell_id AND NOT t.reusable
    ) AND EXISTS (
        SELECT 1 FROM sequencing_sequencingrun
        WHERE flow_cell_id = NEW.flow_cell_id AND voided_at IS NULL AND status <> 'aborted'
    ) THEN
        RAISE EXCEPTION 'A single-use flow cell serves only one run'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""

# The member of a barcode result must belong to the library of the run.
RESULT_RULES_SQL = """
    IF NOT EXISTS (
        SELECT 1 FROM sequencing_librarymember m JOIN sequencing_sequencingrun r
            ON r.library_id = m.library_id
        WHERE m.id = NEW.member_id AND r.id = NEW.run_id
    ) THEN
        RAISE EXCEPTION 'The barcode result must concern a member of the library of the run'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""

# Once a run is completed, its results change only with a reason.
REASON_ON_COMPLETED_RUN_SQL = """
    IF EXISTS (
        SELECT 1 FROM sequencing_sequencingrun WHERE id = NEW.run_id AND status = 'completed'
    ) AND btrim(coalesce(
            nullif(current_setting('pghistory.context_metadata', true), '')::jsonb ->> 'reason', ''
        )) = '' THEN
        RAISE EXCEPTION 'A reason is required to change a result of a completed run'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""


@pghistory.track(*tracked())
class Library(UUIDPrimaryKeyModel, HumanIdentifiedModel, VoidableModel):
    """A pooled sequencing library: barcoded samples prepared with one kit, loaded together."""

    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="libraries", verbose_name=_("project")
    )
    kit = models.ForeignKey(
        LibraryKit, on_delete=models.PROTECT, related_name="libraries", verbose_name=_("kit")
    )
    name = models.CharField(_("name"), max_length=150, blank=True)
    status = models.CharField(
        _("status"), max_length=16, choices=LibraryStatus.choices, default=LibraryStatus.DRAFT
    )
    prepared_on = models.DateField(_("date prepared"), null=True, blank=True)
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("prepared by"),
    )
    reconstructed = models.BooleanField(
        _("reconstructed from a publication"),
        default=False,
        help_text=_("Barcodes and date come from a publication and may be unknown."),
    )
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LibraryQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["-created_at"]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_library_identity",
                fields=["identifier", "project", "kit", "reconstructed"],
            ),
        ]

    def identifier_type_code(self) -> str:
        return LIBRARY_TYPE_CODE

    def __str__(self) -> str:
        return f"{self.identifier} {self.name}".strip()


@pghistory.track(*tracked())
class LibraryMember(UUIDPrimaryKeyModel, VoidableModel):
    """One sample in a library, with its barcode and the quantity put in."""

    library = models.ForeignKey(
        Library, on_delete=models.PROTECT, related_name="members", verbose_name=_("library")
    )
    sample = models.ForeignKey(
        Sample,
        on_delete=models.PROTECT,
        related_name="library_memberships",
        verbose_name=_("sample"),
    )
    barcode = models.PositiveSmallIntegerField(_("barcode"), null=True, blank=True)
    input_ng = models.DecimalField(
        _("input (ng)"), max_digits=9, decimal_places=2, null=True, blank=True
    )
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LibraryMemberQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["library", "barcode", "created_at"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["library", "barcode"],
                condition=models.Q(voided_at__isnull=True, barcode__isnull=False),
                name="one_active_member_per_barcode",
            ),
            models.UniqueConstraint(
                fields=["library", "sample"],
                condition=models.Q(voided_at__isnull=True),
                name="one_active_member_per_sample",
            ),
            models.CheckConstraint(
                condition=models.Q(barcode__isnull=True) | models.Q(barcode__gte=1),
                name="member_barcode_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(input_ng__isnull=True) | models.Q(input_ng__gt=0),
                name="member_input_positive",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_member_place", fields=["library", "sample"]),
            pgtrigger.Trigger(
                name="check_member_rules",
                when=pgtrigger.Before,
                operation=pgtrigger.Insert | pgtrigger.UpdateOf("barcode", "input_ng"),
                func=pgtrigger.Func(MEMBER_RULES_SQL),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.library_id} {self.barcode or '-'}"


@pghistory.track(*tracked())
class FlowCell(UUIDPrimaryKeyModel, VoidableModel):
    """One physical flow cell, identified by its serial number (``PAW12345``)."""

    flow_cell_id = models.CharField(_("flow cell ID"), max_length=50)
    type = models.ForeignKey(
        FlowCellType, on_delete=models.PROTECT, related_name="flow_cells", verbose_name=_("type")
    )
    pores_at_qc = models.PositiveIntegerField(_("active pores at check"), null=True, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(VoidableModel.Meta):
        ordering = ["flow_cell_id"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["flow_cell_id"],
                condition=models.Q(voided_at__isnull=True),
                name="unique_active_flow_cell_id",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_flow_cell_identity", fields=["flow_cell_id", "type"]
            ),
        ]

    def __str__(self) -> str:
        return self.flow_cell_id


@pghistory.track(*tracked())
class SequencingRun(UUIDPrimaryKeyModel, HumanIdentifiedModel, VoidableModel):
    """A library sequenced on a flow cell, with the basecalling that produced the reads."""

    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="sequencing_runs", verbose_name=_("project")
    )
    library = models.ForeignKey(
        Library, on_delete=models.PROTECT, related_name="runs", verbose_name=_("library")
    )
    flow_cell = models.ForeignKey(
        FlowCell, on_delete=models.PROTECT, related_name="runs", verbose_name=_("flow cell")
    )
    instrument = models.ForeignKey(
        Instrument,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="runs",
        verbose_name=_("instrument"),
    )
    pores_at_start = models.PositiveIntegerField(
        _("active pores at start"),
        null=True,
        blank=True,
        help_text=_(
            "Counted at the check before this run; a reused flow cell has fewer each time."
        ),
    )
    run_id = models.CharField(
        _("run ID"),
        max_length=64,
        blank=True,
        help_text=_("The identifier given by the sequencer software."),
    )
    status = models.CharField(
        _("status"), max_length=16, choices=RunStatus.choices, default=RunStatus.PLANNED
    )
    started_on = models.DateField(_("start date"), null=True, blank=True)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("performed by"),
    )
    acquisition_software = models.CharField(_("acquisition software"), max_length=100, blank=True)
    basecaller = models.CharField(_("basecaller"), max_length=50, blank=True)
    basecaller_version = models.CharField(_("basecaller version"), max_length=50, blank=True)
    basecall_model = models.CharField(_("basecalling model"), max_length=150, blank=True)
    total_reads = models.PositiveBigIntegerField(_("reads"), null=True, blank=True)
    total_bases = models.PositiveBigIntegerField(_("bases"), null=True, blank=True)
    reconstructed = models.BooleanField(
        _("reconstructed from a publication"),
        default=False,
        help_text=_("Details come from a publication and may be unknown."),
    )
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SequencingRunQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["-started_on", "identifier"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["flow_cell"],
                condition=models.Q(voided_at__isnull=True, status__in=["planned", "running"]),
                name="one_run_in_progress_per_flow_cell",
            ),
            models.UniqueConstraint(
                fields=["run_id"],
                condition=models.Q(voided_at__isnull=True) & ~models.Q(run_id=""),
                name="unique_active_sequencer_run_id",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_sequencing_run_identity",
                fields=["identifier", "project", "library", "flow_cell"],
            ),
            pgtrigger.Trigger(
                name="check_run_rules",
                when=pgtrigger.Before,
                operation=pgtrigger.Insert,
                func=pgtrigger.Func(RUN_RULES_SQL),
            ),
            RequireReason(
                name="require_reason_to_reopen_run",
                condition=pgtrigger.Condition(
                    "OLD.status = 'completed' AND NEW.status <> 'completed'"
                ),
            ),
        ]

    def identifier_type_code(self) -> str:
        return SEQUENCING_RUN_TYPE_CODE

    def __str__(self) -> str:
        return f"{self.identifier} {self.run_id}".strip()


@pghistory.track(*tracked())
class BarcodeResult(UUIDPrimaryKeyModel, VoidableModel):
    """Reads obtained for one library member in a run, after demultiplexing."""

    run = models.ForeignKey(
        SequencingRun, on_delete=models.PROTECT, related_name="results", verbose_name=_("run")
    )
    member = models.ForeignKey(
        LibraryMember, on_delete=models.PROTECT, related_name="results", verbose_name=_("member")
    )
    reads = models.PositiveBigIntegerField(_("reads"))
    bases = models.PositiveBigIntegerField(_("bases"), null=True, blank=True)
    n50 = models.PositiveIntegerField(_("read N50 (bp)"), null=True, blank=True)
    mean_quality = models.DecimalField(
        _("mean quality (Q)"), max_digits=4, decimal_places=1, null=True, blank=True
    )
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = BarcodeResultQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["run", "member"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["run", "member"],
                condition=models.Q(voided_at__isnull=True),
                name="one_active_result_per_member",
            ),
            models.CheckConstraint(
                condition=models.Q(mean_quality__isnull=True) | models.Q(mean_quality__gt=0),
                name="result_quality_positive",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_result_place", fields=["run", "member"]),
            pgtrigger.Trigger(
                name="check_result_rules",
                when=pgtrigger.Before,
                operation=pgtrigger.Insert,
                func=pgtrigger.Func(RESULT_RULES_SQL),
            ),
            pgtrigger.Trigger(
                name="require_reason_on_completed_run",
                when=pgtrigger.Before,
                operation=pgtrigger.Update,
                condition=pgtrigger.AnyChange("reads", "bases", "n50", "mean_quality"),
                func=pgtrigger.Func(REASON_ON_COMPLETED_RUN_SQL),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id} {self.member_id}"
