# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
from django.db import models
from django.utils.translation import gettext_lazy as _

from poresira.core.models import Project, UUIDPrimaryKeyModel
from poresira.core.voiding import VoidableModel, VoidableQuerySet

from .sample import Sample


class SequenceTopology(models.TextChoices):
    LINEAR = "linear", _("Linear")
    CIRCULAR = "circular", _("Circular")


class SequenceRecordQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "SequenceRecordQuerySet":
        return self.filter(sample__project__in=Project.objects.visible_to(user).values("pk"))


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(condition=pghistory.AnyChange(exclude_auto=True)),
)
class SequenceRecord(UUIDPrimaryKeyModel, VoidableModel):
    """A public sequence (a viral genome in GenBank) obtained from a sample.

    Stands for the sequence until the ``deposits`` app can track submissions; the accession
    and the BioSample are those assigned by INSDC.
    """

    sample = models.ForeignKey(
        Sample,
        on_delete=models.PROTECT,
        related_name="sequence_records",
        verbose_name=_("sample"),
        help_text=_("The material that was sequenced."),
    )
    accession = models.CharField(_("accession"), max_length=16)
    version = models.PositiveSmallIntegerField(_("version"), default=1)
    organism = models.CharField(_("organism"), max_length=200)
    isolate = models.CharField(_("isolate"), max_length=200, blank=True)
    length_bp = models.PositiveIntegerField(_("length (bp)"))
    topology = models.CharField(_("topology"), max_length=8, choices=SequenceTopology.choices)
    assembly_method = models.CharField(_("assembly method"), max_length=100, blank=True)
    biosample_accession = models.CharField(_("BioSample"), max_length=20, blank=True)
    bioproject_accession = models.CharField(_("BioProject"), max_length=20, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SequenceRecordQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["accession"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["accession"],
                condition=models.Q(voided_at__isnull=True),
                name="unique_active_sequence_accession",
            ),
            models.CheckConstraint(
                condition=models.Q(length_bp__gt=0), name="sequence_length_positive"
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_sequence_identity",
                fields=["sample", "accession", "version", "length_bp"],
            ),
        ]

    @property
    def ncbi_url(self) -> str:
        return f"https://www.ncbi.nlm.nih.gov/nuccore/{self.accession}.{self.version}"

    def __str__(self) -> str:
        return f"{self.accession}.{self.version}"
