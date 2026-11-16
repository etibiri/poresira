# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
from django.db import models
from django.utils.translation import gettext_lazy as _

from poresira.core.models import Project, UUIDPrimaryKeyModel
from poresira.core.tracking import tracked
from poresira.core.voiding import VoidableModel, VoidableQuerySet
from poresira.samples.models import Sample

from .deposit import EXPERIMENT_ACCESSION, RUN_ACCESSION, SAMPLE_ACCESSION, STUDY_ACCESSION


class AccessionKind(models.TextChoices):
    BIOPROJECT = "bioproject", _("BioProject")
    BIOSAMPLE = "biosample", _("BioSample")
    SRA_EXPERIMENT = "sra_experiment", _("SRA experiment")
    SRA_RUN = "sra_run", _("SRA run")


class PublicAccessionQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "PublicAccessionQuerySet":
        return self.filter(project__in=Project.objects.visible_to(user).values("pk"))


# The sample of an accession belongs to the project of the accession.
PUBLIC_ACCESSION_SQL = """
    IF NEW.sample_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM samples_sample WHERE id = NEW.sample_id AND project_id = NEW.project_id
    ) THEN
        RAISE EXCEPTION 'A public accession belongs to the project of its sample'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""


@pghistory.track(*tracked())
class PublicAccession(UUIDPrimaryKeyModel, VoidableModel):
    """An accession that already exists in a public archive (NCBI, ENA or DDBJ), however it
    got there: a BioProject, the BioSample of a field sample, or its reads in SRA.

    The archives exchange their records, so such an accession is also valid in the others. A
    deposit never registers again what is recorded here."""

    project = models.ForeignKey(
        Project,
        on_delete=models.PROTECT,
        related_name="public_accessions",
        verbose_name=_("project"),
    )
    kind = models.CharField(_("kind"), max_length=16, choices=AccessionKind.choices)
    accession = models.CharField(_("accession"), max_length=20)
    sample = models.ForeignKey(
        Sample,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="public_accessions",
        verbose_name=_("sample"),
        help_text=_("The field sample or living plant; empty for a BioProject."),
    )
    released_on = models.DateField(_("released on"), null=True, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PublicAccessionQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["kind", "accession"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["kind", "accession"],
                condition=models.Q(voided_at__isnull=True),
                name="one_active_public_accession",
            ),
            models.CheckConstraint(
                condition=models.Q(kind="bioproject", accession__regex=STUDY_ACCESSION)
                | models.Q(kind="biosample", accession__regex=SAMPLE_ACCESSION)
                | models.Q(kind="sra_experiment", accession__regex=EXPERIMENT_ACCESSION)
                | models.Q(kind="sra_run", accession__regex=RUN_ACCESSION),
                name="public_accession_format",
            ),
            models.CheckConstraint(
                condition=models.Q(kind="bioproject", sample__isnull=True)
                | ~models.Q(kind="bioproject") & models.Q(sample__isnull=False),
                name="public_accession_sample_matches_kind",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_public_accession",
                fields=["project", "kind", "accession", "sample"],
            ),
            pgtrigger.Trigger(
                name="check_public_accession_rules",
                when=pgtrigger.Before,
                operation=pgtrigger.Insert,
                func=pgtrigger.Func(PUBLIC_ACCESSION_SQL),
            ),
        ]

    @property
    def archive_name(self) -> str:
        """Which archive gave it: PRJN and SAMN, SRR and SRX are NCBI; PRJE, SAME, ERR and ERX
        are ENA; PRJD, SAMD, DRR and DRX are DDBJ."""
        letter = self.accession[3] if self.accession[:3] in ("PRJ", "SAM") else self.accession[0]
        return {"N": "NCBI", "S": "NCBI", "E": "ENA", "D": "DDBJ"}[letter]

    def __str__(self) -> str:
        return f"{self.kind} {self.accession}"
