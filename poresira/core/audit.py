# SPDX-License-Identifier: AGPL-3.0-or-later
from dataclasses import dataclass

import pghistory
from django.db import connection
from django.utils.translation import gettext_lazy as _

from .models import AuditChainLink


@dataclass(frozen=True)
class AuditChainReport:
    link_count: int
    head_hash: str
    problems: list[tuple[int | None, str]]

    @property
    def is_intact(self) -> bool:
        return not self.problems


def verify_audit_chain() -> AuditChainReport:
    """Recompute every link in the database and report each break found."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT link_seq, problem FROM poresira_audit_chain_verify()")
        problems = list(cursor.fetchall())
    last_link = AuditChainLink.objects.order_by("-seq").first()
    return AuditChainReport(
        link_count=AuditChainLink.objects.count(),
        head_hash=last_link.row_hash if last_link else "",
        problems=problems,
    )


def with_reason(reason: str):
    """Context manager attaching a mandatory reason to every audit entry written inside it."""
    reason = reason.strip()
    if not reason:
        raise ValueError(_("A reason is required for this change."))
    return pghistory.context(reason=reason)
