# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
from django.db import models
from django.utils.translation import gettext_lazy as _

from poresira.core.models import UUIDPrimaryKeyModel
from poresira.core.voiding import VoidableModel


class StorageLocationKind(models.TextChoices):
    ROOM = "room", _("Room")
    FREEZER = "freezer", _("Freezer")
    FRIDGE = "fridge", _("Refrigerator")
    CABINET = "cabinet", _("Cabinet")
    SHELF = "shelf", _("Shelf")
    RACK = "rack", _("Rack")
    BOX = "box", _("Box")


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(condition=pghistory.AnyChange(exclude_auto=True)),
)
class StorageLocation(UUIDPrimaryKeyModel, VoidableModel):
    """A node of the storage tree. A box with rows and columns offers grid positions (A1...)."""

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        verbose_name=_("parent"),
    )
    name = models.CharField(_("name"), max_length=100)
    kind = models.CharField(_("type"), max_length=16, choices=StorageLocationKind.choices)
    nominal_temperature_c = models.SmallIntegerField(
        _("nominal temperature (°C)"), null=True, blank=True
    )
    grid_rows = models.PositiveSmallIntegerField(_("grid rows"), null=True, blank=True)
    grid_columns = models.PositiveSmallIntegerField(_("grid columns"), null=True, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta(VoidableModel.Meta):
        ordering = ["name"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["parent", "name"],
                condition=models.Q(voided_at__isnull=True),
                nulls_distinct=False,
                name="unique_active_storage_name_per_parent",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(grid_rows__isnull=True, grid_columns__isnull=True)
                    | models.Q(
                        grid_rows__gt=0,
                        grid_columns__gt=0,
                        grid_rows__isnull=False,
                        grid_columns__isnull=False,
                    )
                ),
                name="storage_grid_needs_rows_and_columns",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_parent", fields=["parent"]),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def path(self) -> str:
        names, node = [], self
        while node is not None:
            names.append(node.name)
            node = node.parent
        return " > ".join(reversed(names))
