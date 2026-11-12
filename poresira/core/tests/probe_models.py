# SPDX-License-Identifier: AGPL-3.0-or-later
"""A throwaway model to exercise reusable triggers; its table is created inside each test."""

import pgtrigger
from django.db import models

from poresira.core.pgtriggers import RequireReason


# Meta.triggers is only collected at startup; the decorator also works for late models.
@pgtrigger.register(RequireReason(fields=["amount"]))
class ReasonProbe(models.Model):  # noqa: DJ008
    amount = models.IntegerField()
    note = models.CharField(max_length=50, blank=True)

    class Meta:
        app_label = "core"
