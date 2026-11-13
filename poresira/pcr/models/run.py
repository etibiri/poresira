# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from poresira.core.models import HumanIdentifiedModel, Project, UUIDPrimaryKeyModel
from poresira.core.pgtriggers import RequireReason
from poresira.core.voiding import VoidableModel, VoidableQuerySet
from poresira.samples.models import Sample

from .reference import Assay, tracked

PCR_RUN_TYPE_CODE = "T"
PLATE_ROWS, PLATE_COLUMNS = 8, 12  # a 96-well plate: A1 to H12
MAX_TUBES = 96


class Container(models.TextChoices):
    PLATE_96 = "plate_96", _("96-well plate")
    TUBES = "tubes", _("PCR tubes")


class RunStatus(models.TextChoices):
    PLANNED = "planned", _("Planned")
    PERFORMED = "performed", _("Performed")
    CLOSED = "closed", _("Closed")


class Role(models.TextChoices):
    SAMPLE = "sample", _("Sample")
    POSITIVE_CONTROL = "positive_control", _("Positive control")
    NEGATIVE_CONTROL = "negative_control", _("No-template control")
    EXTRACTION_BLANK = "extraction_blank", _("Extraction blank")


class Result(models.TextChoices):
    PENDING = "pending", _("Pending")
    POSITIVE = "positive", _("Positive")
    NEGATIVE = "negative", _("Negative")
    INCONCLUSIVE = "inconclusive", _("Inconclusive")


class PcrRunQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "PcrRunQuerySet":
        return self.filter(project__in=Project.objects.visible_to(user).values("pk"))


class PcrReactionQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "PcrReactionQuerySet":
        return self.filter(run__project__in=Project.objects.visible_to(user).values("pk"))


# Once a run is closed, its results are only corrected with a reason; the run reopens likewise.
REASON_ON_CLOSED_RUN_SQL = """
    IF EXISTS (SELECT 1 FROM pcr_pcrrun WHERE id = NEW.run_id AND status = 'closed')
        AND btrim(coalesce(
            nullif(current_setting('pghistory.context_metadata', true), '')::jsonb ->> 'reason', ''
        )) = '' THEN
        RAISE EXCEPTION 'A reason is required to change a result of a closed run'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""

# The template must belong to the run's project and must be a physical extract or product.
TEMPLATE_RULES_SQL = """
    IF NEW.sample_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM samples_sample s JOIN pcr_pcrrun r ON r.id = NEW.run_id
        WHERE s.id = NEW.sample_id AND s.project_id <> r.project_id
    ) THEN
        RAISE EXCEPTION 'The template of a reaction must belong to the project of its run'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.sample_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM samples_sample WHERE id = NEW.sample_id AND kind = 'living_plant'
    ) THEN
        RAISE EXCEPTION 'A living plant cannot be a PCR template; use an extract of it'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""


@pghistory.track(*tracked())
class PcrRun(UUIDPrimaryKeyModel, HumanIdentifiedModel, VoidableModel):
    """One pass through a thermocycler: an assay applied to reactions in a plate or in tubes."""

    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="pcr_runs", verbose_name=_("project")
    )
    assay = models.ForeignKey(
        Assay, on_delete=models.PROTECT, related_name="runs", verbose_name=_("assay")
    )
    container = models.CharField(
        _("container"), max_length=16, choices=Container.choices, default=Container.PLATE_96
    )
    tube_count = models.PositiveSmallIntegerField(
        _("number of tubes"), null=True, blank=True, help_text=_("Only for PCR tubes.")
    )
    status = models.CharField(
        _("status"), max_length=16, choices=RunStatus.choices, default=RunStatus.PLANNED
    )
    performed_on = models.DateField(_("date"), null=True, blank=True)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("performed by"),
    )
    thermocycler = models.CharField(_("thermocycler"), max_length=100, blank=True)
    master_mix_lot = models.CharField(_("master mix lot"), max_length=100, blank=True)
    polymerase_lot = models.CharField(_("polymerase lot"), max_length=100, blank=True)
    reconstructed = models.BooleanField(
        _("reconstructed from a publication"),
        default=False,
        help_text=_("Results come from a published table; date, layout and controls are unknown."),
    )
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PcrRunQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["-performed_on", "identifier"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.CheckConstraint(
                condition=models.Q(container=Container.PLATE_96, tube_count__isnull=True)
                | models.Q(
                    container=Container.TUBES,
                    tube_count__isnull=False,
                    tube_count__gte=1,
                    tube_count__lte=MAX_TUBES,
                ),
                name="pcr_run_tube_count_matches_container",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_pcr_run_identity",
                fields=["identifier", "project", "assay", "container", "tube_count"],
            ),
            RequireReason(
                name="require_reason_to_reopen_run",
                condition=pgtrigger.Condition("OLD.status = 'closed' AND NEW.status <> 'closed'"),
            ),
        ]

    def identifier_type_code(self) -> str:
        return PCR_RUN_TYPE_CODE

    def __str__(self) -> str:
        return f"{self.identifier} {self.assay_id}"


@pghistory.track(*tracked())
class PcrReaction(UUIDPrimaryKeyModel, VoidableModel):
    """One well or tube of a run, with its template and its outcome.

    ``position`` is ``A1`` to ``H12`` in a plate, ``1`` to ``tube_count`` in tubes.
    """

    run = models.ForeignKey(
        PcrRun, on_delete=models.PROTECT, related_name="reactions", verbose_name=_("run")
    )
    position = models.CharField(_("position"), max_length=8)
    role = models.CharField(_("role"), max_length=16, choices=Role.choices, default=Role.SAMPLE)
    sample = models.ForeignKey(
        Sample,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pcr_reactions",
        verbose_name=_("template"),
    )
    template_volume_ul = models.DecimalField(
        _("template volume (µL)"), max_digits=6, decimal_places=2, null=True, blank=True
    )
    result = models.CharField(
        _("result"), max_length=16, choices=Result.choices, default=Result.PENDING
    )
    ct = models.DecimalField(
        _("Ct"), max_digits=5, decimal_places=2, null=True, blank=True, help_text=_("qPCR only.")
    )
    band_size_bp = models.PositiveIntegerField(_("band size on gel (bp)"), null=True, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PcrReactionQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["run", "created_at"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["run", "position"],
                condition=models.Q(voided_at__isnull=True),
                name="one_active_reaction_per_position",
            ),
            models.CheckConstraint(
                condition=~models.Q(role=Role.SAMPLE) | models.Q(sample__isnull=False),
                name="pcr_sample_reaction_has_a_template",
            ),
            models.CheckConstraint(
                condition=models.Q(ct__isnull=True) | models.Q(ct__gt=0),
                name="pcr_ct_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(band_size_bp__isnull=True) | models.Q(band_size_bp__gt=0),
                name="pcr_band_size_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(template_volume_ul__isnull=True)
                | models.Q(template_volume_ul__gt=0),
                name="pcr_template_volume_positive",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_reaction_place", fields=["run", "position"]),
            pgtrigger.Trigger(
                name="check_reaction_template",
                when=pgtrigger.Before,
                operation=pgtrigger.Insert | pgtrigger.UpdateOf("sample_id"),
                func=pgtrigger.Func(TEMPLATE_RULES_SQL),
            ),
            pgtrigger.Trigger(
                name="require_reason_on_closed_run",
                when=pgtrigger.Before,
                operation=pgtrigger.Update,
                condition=pgtrigger.AnyChange("result", "ct", "band_size_bp", "role", "sample"),
                func=pgtrigger.Func(REASON_ON_CLOSED_RUN_SQL),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id} {self.position}"
