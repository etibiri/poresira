# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared sequencing reference data: library kits, flow cell types and instruments.

Reference records are never edited in place once defined, so that every past run still points at
exactly what was used. One adapter per platform (see ``adapters``) knows the rules of each.
"""

import pghistory
import pgtrigger
from django.db import models
from django.utils.translation import gettext_lazy as _

from poresira.core.models import UUIDPrimaryKeyModel
from poresira.core.tracking import tracked
from poresira.core.voiding import VoidableModel


class Platform(models.TextChoices):
    NANOPORE = "nanopore", _("Oxford Nanopore")


class Chemistry(models.TextChoices):
    NATIVE_BARCODING = "native_barcoding", _("Native barcoding")
    RAPID_BARCODING = "rapid_barcoding", _("Rapid barcoding")
    CDNA_PCR_BARCODING = "cdna_pcr_barcoding", _("cDNA-PCR barcoding")
    LIGATION = "ligation", _("Ligation (no barcodes)")
    OTHER = "other", _("Other")


@pghistory.track(*tracked())
class LibraryKit(UUIDPrimaryKeyModel, VoidableModel):
    """A library preparation kit, for example ``SQK-NBD114.96``."""

    platform = models.CharField(
        _("platform"), max_length=16, choices=Platform.choices, default=Platform.NANOPORE
    )
    code = models.CharField(_("kit code"), max_length=40)
    name = models.CharField(_("name"), max_length=150)
    chemistry = models.CharField(_("chemistry"), max_length=20, choices=Chemistry.choices)
    barcode_count = models.PositiveSmallIntegerField(
        _("number of barcodes"),
        null=True,
        blank=True,
        help_text=_("Empty for a kit without barcodes."),
    )
    reference = models.CharField(_("reference"), max_length=300, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta(VoidableModel.Meta):
        ordering = ["code"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(voided_at__isnull=True),
                name="unique_active_kit_code",
            ),
            models.CheckConstraint(
                condition=models.Q(barcode_count__isnull=True) | models.Q(barcode_count__gte=1),
                name="kit_barcode_count_positive",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_kit_definition", fields=["platform", "code", "barcode_count"]
            ),
        ]

    def __str__(self) -> str:
        return self.code


@pghistory.track(*tracked())
class FlowCellType(UUIDPrimaryKeyModel, VoidableModel):
    """A kind of flow cell, for example ``FLO-MIN114`` (R10.4.1) or ``FLO-FLG001`` (Flongle)."""

    platform = models.CharField(
        _("platform"), max_length=16, choices=Platform.choices, default=Platform.NANOPORE
    )
    code = models.CharField(_("product code"), max_length=40)
    name = models.CharField(_("name"), max_length=150)
    pore = models.CharField(_("pore type"), max_length=40, blank=True)
    reusable = models.BooleanField(
        _("reusable"),
        default=True,
        help_text=_("A reusable flow cell can be washed and loaded again (MinION, PromethION)."),
    )
    notes = models.TextField(_("notes"), blank=True)

    class Meta(VoidableModel.Meta):
        ordering = ["code"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(voided_at__isnull=True),
                name="unique_active_flow_cell_type_code",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_flow_cell_type_code", fields=["platform", "code"]),
        ]

    def __str__(self) -> str:
        return self.code


@pghistory.track(*tracked())
class Instrument(UUIDPrimaryKeyModel, VoidableModel):
    """A sequencer, for example a MinION Mk1C."""

    platform = models.CharField(
        _("platform"), max_length=16, choices=Platform.choices, default=Platform.NANOPORE
    )
    name = models.CharField(_("name"), max_length=100)
    model = models.CharField(_("model"), max_length=100)
    serial_number = models.CharField(_("serial number"), max_length=100, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta(VoidableModel.Meta):
        ordering = ["name"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(voided_at__isnull=True),
                name="unique_active_instrument_name",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_instrument_platform", fields=["platform"]),
        ]

    def __str__(self) -> str:
        return self.name
