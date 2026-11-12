# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from poresira.core.models import Project, UUIDPrimaryKeyModel
from poresira.core.voiding import VoidableModel, VoidableQuerySet

from .sample import Sample


class QcMethod(models.TextChoices):
    NANODROP = "nanodrop", _("NanoDrop")
    QUBIT = "qubit", _("Qubit")
    QUBIT_RNA_IQ = "qubit_rna_iq", _("Qubit RNA IQ")
    GEL = "gel", _("Gel electrophoresis")
    OTHER = "other", _("Other")


class QcOutcome(models.TextChoices):
    PASSED = "passed", _("Passed")
    FAILED = "failed", _("Failed")
    NOT_ASSESSED = "not_assessed", _("Not assessed")


MEASURED_FIELDS = ["concentration_ng_ul", "a260_280", "a260_230", "integrity_number"]


class QcMeasurementQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "QcMeasurementQuerySet":
        return self.filter(sample__project__in=Project.objects.visible_to(user).values("pk"))


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(condition=pghistory.AnyChange(exclude_auto=True)),
)
class QcMeasurement(UUIDPrimaryKeyModel, VoidableModel):
    """One instrument reading. Readings are never edited: void a wrong one and record again."""

    sample = models.ForeignKey(
        Sample, on_delete=models.PROTECT, related_name="qc_measurements", verbose_name=_("sample")
    )
    method = models.CharField(_("method"), max_length=16, choices=QcMethod.choices)
    measured_at = models.DateTimeField(_("measured at"), default=timezone.now)
    measured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("measured by"),
    )
    concentration_ng_ul = models.DecimalField(
        _("concentration (ng/µL)"), max_digits=10, decimal_places=3, null=True, blank=True
    )
    a260_280 = models.DecimalField(
        _("A260/280"), max_digits=5, decimal_places=2, null=True, blank=True
    )
    a260_230 = models.DecimalField(
        _("A260/230"), max_digits=5, decimal_places=2, null=True, blank=True
    )
    integrity_number = models.DecimalField(
        _("integrity number"),
        max_digits=3,
        decimal_places=1,
        null=True,
        blank=True,
        help_text=_("RNA integrity score such as RIN, RQN or the Qubit IQ value."),
    )
    outcome = models.CharField(
        _("outcome"), max_length=16, choices=QcOutcome.choices, default=QcOutcome.NOT_ASSESSED
    )
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = QcMeasurementQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["-measured_at"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.CheckConstraint(
                condition=(
                    models.Q(concentration_ng_ul__isnull=False)
                    | models.Q(a260_280__isnull=False)
                    | models.Q(a260_230__isnull=False)
                    | models.Q(integrity_number__isnull=False)
                    | ~models.Q(outcome=QcOutcome.NOT_ASSESSED)
                ),
                name="qc_measurement_has_a_result",
            ),
            models.CheckConstraint(
                condition=models.Q(concentration_ng_ul__isnull=True)
                | models.Q(concentration_ng_ul__gte=0),
                name="qc_concentration_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(integrity_number__isnull=True)
                | models.Q(integrity_number__gte=0, integrity_number__lte=10),
                name="qc_integrity_number_in_range",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_measurement",
                fields=["sample", "method", "measured_at", "outcome", *MEASURED_FIELDS],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sample_id} {self.method}"
