# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared PCR reference data: primers, cycling programs, master mixes and assays.

Primers, programs and recipes are never edited in place once defined: a change is a new record,
so that every past run still points at exactly what was used.
"""

import pghistory
import pgtrigger
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from poresira.core.models import UUIDPrimaryKeyModel
from poresira.core.tracking import tracked
from poresira.core.voiding import VoidableModel

IUPAC_DNA = r"^[ACGTRYSWKMBDHVN]+$"


class PrimerDirection(models.TextChoices):
    FORWARD = "forward", _("Forward")
    REVERSE = "reverse", _("Reverse")


@pghistory.track(*tracked())
class Primer(UUIDPrimaryKeyModel, VoidableModel):
    """An oligonucleotide. Degenerate bases use IUPAC codes (K, W, R, V, Y...)."""

    name = models.CharField(_("name"), max_length=100)
    sequence = models.CharField(
        _("sequence (5' to 3')"),
        max_length=200,
        validators=[RegexValidator(IUPAC_DNA, _("Use upper-case IUPAC DNA letters only."))],
    )
    direction = models.CharField(_("direction"), max_length=8, choices=PrimerDirection.choices)
    target = models.CharField(_("target"), max_length=200, blank=True)
    reference = models.CharField(_("reference"), max_length=300, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta(VoidableModel.Meta):
        ordering = ["name"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(voided_at__isnull=True),
                name="unique_active_primer_name",
            ),
            models.CheckConstraint(
                condition=models.Q(sequence__regex=IUPAC_DNA), name="primer_sequence_is_iupac"
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_primer_sequence", fields=["sequence", "direction"]),
        ]

    @property
    def length(self) -> int:
        return len(self.sequence)

    def __str__(self) -> str:
        return self.name


@pghistory.track(*tracked())
class CyclingProgram(UUIDPrimaryKeyModel, VoidableModel):
    """A thermocycler program: initial step, ``cycles`` repeats of the cycle steps, final step."""

    name = models.CharField(_("name"), max_length=100)
    cycles = models.PositiveSmallIntegerField(_("number of cycles"))
    reference = models.CharField(_("reference"), max_length=300, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta(VoidableModel.Meta):
        ordering = ["name"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(voided_at__isnull=True),
                name="unique_active_program_name",
            ),
            models.CheckConstraint(condition=models.Q(cycles__gte=1), name="program_has_a_cycle"),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_program_cycles", fields=["cycles"]),
        ]

    def __str__(self) -> str:
        return self.name


class StepStage(models.TextChoices):
    INITIAL = "initial", _("Initial")
    CYCLE = "cycle", _("Cycle")
    FINAL = "final", _("Final")


@pghistory.track(*tracked())
class CyclingStep(UUIDPrimaryKeyModel):
    """One temperature plateau of a program. Steps are fixed once written and never deleted."""

    program = models.ForeignKey(
        CyclingProgram, on_delete=models.PROTECT, related_name="steps", verbose_name=_("program")
    )
    order = models.PositiveSmallIntegerField(_("order"))
    stage = models.CharField(_("stage"), max_length=8, choices=StepStage.choices)
    label = models.CharField(_("label"), max_length=50, blank=True)
    temperature_c = models.DecimalField(_("temperature (°C)"), max_digits=4, decimal_places=1)
    duration_seconds = models.PositiveIntegerField(_("duration (s)"))

    class Meta:
        ordering = ["program", "order"]
        constraints = [
            models.UniqueConstraint(fields=["program", "order"], name="unique_step_order"),
            models.CheckConstraint(
                condition=models.Q(temperature_c__gte=0, temperature_c__lte=105),
                name="step_temperature_in_range",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_seconds__gt=0), name="step_duration_positive"
            ),
        ]
        triggers = [
            pgtrigger.Protect(name="protect_step_delete", operation=pgtrigger.Delete),
            pgtrigger.ReadOnly(
                name="read_only_step",
                fields=["program", "order", "stage", "label", "temperature_c", "duration_seconds"],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.program_id} {self.order}"


@pghistory.track(*tracked())
class MasterMix(UUIDPrimaryKeyModel, VoidableModel):
    """A reaction recipe: what goes into one reaction, polymerase included."""

    name = models.CharField(_("name"), max_length=100)
    polymerase = models.CharField(_("polymerase"), max_length=100, blank=True)
    reaction_volume_ul = models.DecimalField(
        _("reaction volume (µL)"), max_digits=6, decimal_places=1
    )
    reference = models.CharField(_("reference"), max_length=300, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta(VoidableModel.Meta):
        ordering = ["name"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(voided_at__isnull=True),
                name="unique_active_master_mix_name",
            ),
            models.CheckConstraint(
                condition=models.Q(reaction_volume_ul__gt=0), name="master_mix_volume_positive"
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_master_mix_volume", fields=["reaction_volume_ul"]),
        ]

    def __str__(self) -> str:
        return self.name


@pghistory.track(*tracked())
class MasterMixComponent(UUIDPrimaryKeyModel):
    """One ingredient and its volume per reaction. Fixed once written and never deleted."""

    master_mix = models.ForeignKey(
        MasterMix,
        on_delete=models.PROTECT,
        related_name="components",
        verbose_name=_("master mix"),
    )
    name = models.CharField(_("ingredient"), max_length=100)
    concentration = models.CharField(
        _("concentration"), max_length=50, blank=True, help_text=_("As written, e.g. 10 µM.")
    )
    volume_ul = models.DecimalField(_("volume per reaction (µL)"), max_digits=6, decimal_places=2)

    class Meta:
        ordering = ["master_mix", "name"]
        constraints = [
            models.UniqueConstraint(fields=["master_mix", "name"], name="unique_component_name"),
            models.CheckConstraint(
                condition=models.Q(volume_ul__gt=0), name="component_volume_positive"
            ),
        ]
        triggers = [
            pgtrigger.Protect(name="protect_component_delete", operation=pgtrigger.Delete),
            pgtrigger.ReadOnly(
                name="read_only_component",
                fields=["master_mix", "name", "concentration", "volume_ul"],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.master_mix_id} {self.name}"


@pghistory.track(*tracked())
class Assay(UUIDPrimaryKeyModel, VoidableModel):
    """A named test: primer pair, program and recipe for one target, e.g. SPFMV CP RT-PCR."""

    name = models.CharField(_("name"), max_length=100)
    target = models.CharField(_("target"), max_length=200)
    forward_primer = models.ForeignKey(
        Primer,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("forward primer"),
    )
    reverse_primer = models.ForeignKey(
        Primer,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("reverse primer"),
    )
    cycling_program = models.ForeignKey(
        CyclingProgram,
        on_delete=models.PROTECT,
        related_name="assays",
        verbose_name=_("cycling program"),
    )
    master_mix = models.ForeignKey(
        MasterMix,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assays",
        verbose_name=_("master mix"),
    )
    reverse_transcription = models.BooleanField(
        _("reverse transcription"),
        default=False,
        help_text=_("The template is RNA that is reverse-transcribed first (RT-PCR)."),
    )
    expected_amplicon_bp = models.PositiveIntegerField(
        _("expected amplicon (bp)"), null=True, blank=True
    )
    reference = models.CharField(_("reference"), max_length=300, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta(VoidableModel.Meta):
        ordering = ["name"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(voided_at__isnull=True),
                name="unique_active_assay_name",
            ),
            models.CheckConstraint(
                condition=models.Q(expected_amplicon_bp__isnull=True)
                | models.Q(expected_amplicon_bp__gt=0),
                name="assay_amplicon_positive",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_assay_design",
                fields=[
                    "forward_primer",
                    "reverse_primer",
                    "cycling_program",
                    "master_mix",
                    "reverse_transcription",
                ],
            ),
        ]

    def __str__(self) -> str:
        return self.name
