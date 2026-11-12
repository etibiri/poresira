# SPDX-License-Identifier: AGPL-3.0-or-later
"""Void instead of delete: domain records are never hard-deleted."""

import pghistory
import pgtrigger
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class HardDeleteForbiddenError(Exception):
    """Raised when code tries to delete a voidable record instead of voiding it."""


class VoidableQuerySet(models.QuerySet):
    def active(self):
        return self.filter(voided_at__isnull=True)

    def delete(self):
        raise HardDeleteForbiddenError("Voidable records cannot be deleted; void them instead.")


class VoidableModel(models.Model):
    """Abstract base: voiding needs a reason; deleting is blocked in Python and in PostgreSQL.

    Subclasses that declare their own ``Meta`` must inherit ``VoidableModel.Meta``
    and keep its constraints and triggers.
    """

    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.PROTECT,
        related_name="+",
    )
    void_reason = models.TextField(blank=True, editable=False)

    objects = VoidableQuerySet.as_manager()

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=models.Q(voided_at__isnull=True, void_reason="")
                | models.Q(voided_at__isnull=False, voided_by__isnull=False)
                & ~models.Q(void_reason=""),
                name="%(app_label)s_%(class)s_void_requires_reason",
            ),
        ]
        triggers = [pgtrigger.Protect(name="protect_delete", operation=pgtrigger.Delete)]

    @property
    def is_voided(self) -> bool:
        return self.voided_at is not None

    def void(self, *, user, reason: str) -> None:
        reason = reason.strip()
        if not reason:
            raise ValueError(_("A reason is required to void a record."))
        if self.is_voided:
            raise ValueError(_("This record is already voided."))
        self.voided_at = timezone.now()
        self.voided_by = user
        self.void_reason = reason
        with pghistory.context(reason=reason):
            self.save(update_fields=["voided_at", "voided_by", "void_reason"])

    def delete(self, *args, **kwargs):
        raise HardDeleteForbiddenError("Voidable records cannot be deleted; void them instead.")
