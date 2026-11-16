# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
from django.db import models
from django.utils.translation import gettext_lazy as _

from poresira.core.models import UUIDPrimaryKeyModel
from poresira.core.pgtriggers import RequireReason
from poresira.core.tracking import tracked
from poresira.core.voiding import VoidableModel


class BackendKind(models.TextChoices):
    LOCAL = "local", _("Local disk")
    NAS = "nas", _("Network share (mounted)")
    OFFLINE = "offline", _("Offline medium (catalogued)")


@pghistory.track(*tracked())
class StorageBackend(UUIDPrimaryKeyModel, VoidableModel):
    """A place where files live. PoreSira records files in place and never copies them."""

    name = models.CharField(_("name"), max_length=100)
    kind = models.CharField(_("kind"), max_length=16, choices=BackendKind.choices)
    root = models.CharField(
        _("root"),
        max_length=500,
        blank=True,
        help_text=_(
            "Absolute directory of a disk or mounted share; the label of the medium when offline."
        ),
    )
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(VoidableModel.Meta):
        ordering = ["name"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(voided_at__isnull=True),
                name="unique_active_backend_name",
            ),
            models.CheckConstraint(
                condition=models.Q(kind="offline", root__gt="")
                | models.Q(kind__in=["local", "nas"], root__startswith="/"),
                name="backend_root_matches_kind",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_backend_kind", fields=["kind"]),
            RequireReason(name="require_reason_to_move_backend", fields=["root"]),
        ]

    @property
    def is_readable(self) -> bool:
        """Offline media cannot be read: their files are catalogued, not hashed here."""
        return self.kind != BackendKind.OFFLINE

    def __str__(self) -> str:
        return self.name
