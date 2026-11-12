# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from poresira.core.models import HumanIdentifiedModel, Project, UUIDPrimaryKeyModel
from poresira.core.voiding import VoidableModel, VoidableQuerySet

COLLECTION_EVENT_TYPE_CODE = "C"


class CollectionEventQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "CollectionEventQuerySet":
        return self.filter(project__in=Project.objects.visible_to(user).values("pk"))


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(condition=pghistory.AnyChange(exclude_auto=True)),
    model_name="CollectionEventHistory",
)
class CollectionEvent(UUIDPrimaryKeyModel, HumanIdentifiedModel, VoidableModel):
    """A visit to a place where samples were taken. The date may be unknown for old samples."""

    project = models.ForeignKey(
        Project,
        on_delete=models.PROTECT,
        related_name="collection_events",
        verbose_name=_("project"),
    )
    collected_on = models.DateField(_("collection date"), null=True, blank=True)
    country = models.CharField(_("country"), max_length=100, blank=True)
    region = models.CharField(_("region"), max_length=100, blank=True)
    province = models.CharField(_("province"), max_length=100, blank=True)
    locality = models.CharField(_("locality"), max_length=100, blank=True)
    site_name = models.CharField(_("farm or field"), max_length=200, blank=True)
    latitude = models.DecimalField(
        _("latitude"),
        max_digits=8,
        decimal_places=5,
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        _("longitude"),
        max_digits=8,
        decimal_places=5,
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    collector = models.CharField(_("collector"), max_length=200, blank=True)
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CollectionEventQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["-collected_on", "identifier"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.CheckConstraint(
                condition=models.Q(latitude__isnull=True, longitude__isnull=True)
                | models.Q(latitude__isnull=False, longitude__isnull=False),
                name="collection_coordinates_come_in_pairs",
            ),
            models.CheckConstraint(
                condition=models.Q(latitude__isnull=True)
                | (models.Q(latitude__gte=-90, latitude__lte=90)),
                name="collection_latitude_in_range",
            ),
            models.CheckConstraint(
                condition=models.Q(longitude__isnull=True)
                | (models.Q(longitude__gte=-180, longitude__lte=180)),
                name="collection_longitude_in_range",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_identity", fields=["identifier", "project"]),
        ]

    def identifier_type_code(self) -> str:
        return COLLECTION_EVENT_TYPE_CODE

    def __str__(self) -> str:
        return f"{self.identifier} {self.locality or self.province or ''}".strip()
