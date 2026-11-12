# SPDX-License-Identifier: AGPL-3.0-or-later
from dataclasses import dataclass

from django.db import connection

from .models import AuditChainLink
from .reason import with_reason

__all__ = ["AuditChainReport", "verify_audit_chain", "with_reason"]


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
