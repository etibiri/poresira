# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from poresira.core.models import HumanIdentifiedModel, Project, UUIDPrimaryKeyModel
from poresira.core.pgtriggers import RequireReason
from poresira.core.voiding import VoidableModel, VoidableQuerySet

from .collection import CollectionEvent
from .storage import StorageLocation


class SampleKind(models.TextChoices):
    FIELD_SAMPLE = "field_sample", _("Field sample")
    LIVING_PLANT = "living_plant", _("Living plant")
    DNA_EXTRACT = "dna_extract", _("DNA extract")
    RNA_EXTRACT = "rna_extract", _("RNA extract")
    AMPLIFICATION_PRODUCT = "amplification_product", _("Amplification product")


# Object type letters in human-readable identifiers (WBF-S26-00123-K).
KIND_TYPE_CODES = {
    SampleKind.FIELD_SAMPLE: "S",
    SampleKind.LIVING_PLANT: "P",
    SampleKind.DNA_EXTRACT: "D",
    SampleKind.RNA_EXTRACT: "R",
    SampleKind.AMPLIFICATION_PRODUCT: "A",
}


class SampleStatus(models.TextChoices):
    REGISTERED = "registered", _("Registered")
    STORED = "stored", _("Stored")
    IN_PROCESS = "in_process", _("In process")
    CONSUMED = "consumed", _("Consumed")
    DISCARDED = "discarded", _("Discarded")


OCCUPYING_STATUSES = [SampleStatus.REGISTERED, SampleStatus.STORED, SampleStatus.IN_PROCESS]

# Changing any of these needs a reason, enforced by a database trigger.
CRITICAL_FIELDS = [
    "concentration_ng_ul",
    "volume_ul",
    "status",
    "barcode",
    "storage_location",
    "position",
]


class SampleQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "SampleQuerySet":
        return self.filter(project__in=Project.objects.visible_to(user).values("pk"))


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(condition=pghistory.AnyChange(exclude_auto=True)),
)
class Sample(UUIDPrimaryKeyModel, HumanIdentifiedModel, VoidableModel):
    """A field sample or anything derived from one. ``parent`` records the lineage."""

    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="samples", verbose_name=_("project")
    )
    collection_event = models.ForeignKey(
        CollectionEvent,
        on_delete=models.PROTECT,
        related_name="samples",
        verbose_name=_("collection event"),
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        verbose_name=_("parent"),
    )
    kind = models.CharField(_("type"), max_length=24, choices=SampleKind.choices)
    label = models.CharField(
        _("label"),
        max_length=100,
        blank=True,
        help_text=_("The laboratory's own sample name, for example BFA266."),
    )
    host_taxon = models.CharField(_("host species"), max_length=200, blank=True)
    host_tax_id = models.PositiveIntegerField(_("host NCBI taxon ID"), null=True, blank=True)
    host_variety = models.CharField(_("host variety"), max_length=200, blank=True)
    symptom_score = models.PositiveSmallIntegerField(
        _("symptom score"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(9)],
        help_text=_("Symptom severity on the 1 to 9 scale of the International Potato Center."),
    )
    symptom_notes = models.CharField(_("symptoms"), max_length=200, blank=True)
    status = models.CharField(
        _("status"), max_length=16, choices=SampleStatus.choices, default=SampleStatus.REGISTERED
    )
    barcode = models.CharField(_("barcode"), max_length=100, blank=True)
    storage_location = models.ForeignKey(
        StorageLocation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="samples",
        verbose_name=_("storage location"),
    )
    position = models.CharField(_("position"), max_length=16, blank=True)
    concentration_ng_ul = models.DecimalField(
        _("concentration (ng/µL)"), max_digits=10, decimal_places=3, null=True, blank=True
    )
    volume_ul = models.DecimalField(
        _("volume (µL)"), max_digits=10, decimal_places=2, null=True, blank=True
    )
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SampleQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["identifier"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.CheckConstraint(
                condition=models.Q(kind=SampleKind.FIELD_SAMPLE, parent__isnull=True)
                | (~models.Q(kind=SampleKind.FIELD_SAMPLE) & models.Q(parent__isnull=False)),
                name="sample_parent_iff_derivative",
            ),
            models.CheckConstraint(
                condition=models.Q(position="") | models.Q(storage_location__isnull=False),
                name="sample_position_needs_location",
            ),
            models.CheckConstraint(
                condition=models.Q(symptom_score__isnull=True)
                | models.Q(symptom_score__gte=1, symptom_score__lte=9),
                name="sample_symptom_score_in_range",
            ),
            models.CheckConstraint(
                condition=models.Q(concentration_ng_ul__isnull=True)
                | models.Q(concentration_ng_ul__gte=0),
                name="sample_concentration_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(volume_ul__isnull=True) | models.Q(volume_ul__gte=0),
                name="sample_volume_not_negative",
            ),
            models.UniqueConstraint(
                fields=["project", "kind", "label"],
                condition=models.Q(voided_at__isnull=True) & ~models.Q(label=""),
                name="unique_active_sample_label_per_kind",
            ),
            models.UniqueConstraint(
                fields=["barcode"],
                condition=models.Q(voided_at__isnull=True) & ~models.Q(barcode=""),
                name="unique_active_sample_barcode",
            ),
            models.UniqueConstraint(
                fields=["storage_location", "position"],
                condition=models.Q(voided_at__isnull=True)
                & ~models.Q(position="")
                & models.Q(status__in=OCCUPYING_STATUSES),
                name="one_active_sample_per_storage_position",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            RequireReason(fields=CRITICAL_FIELDS),
            pgtrigger.ReadOnly(
                name="read_only_identity",
                fields=["identifier", "kind", "parent", "project", "collection_event"],
            ),
        ]

    def identifier_type_code(self) -> str:
        return KIND_TYPE_CODES[self.kind]

    def save(self, *args, **kwargs):
        if self._state.adding and self.parent_id:
            self._check_matches_parent()
        super().save(*args, **kwargs)

    def _check_matches_parent(self) -> None:
        parent = self.parent
        if (parent.project_id, parent.collection_event_id) != (
            self.project_id,
            self.collection_event_id,
        ):
            raise ValueError("A derivative must share its parent's project and collection event.")

    def __str__(self) -> str:
        return f"{self.identifier} {self.label}".strip()
