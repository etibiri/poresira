# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build a readable history for a project from the audit event tables."""

from dataclasses import dataclass, field
from datetime import datetime

from django.utils.translation import gettext_lazy as _

from .models import Project, ProjectEvent, ProjectMembershipEvent, User

PROJECT_FIELDS = ("code", "name", "description", "voided_at", "void_reason")
MEMBERSHIP_FIELDS = ("role", "voided_at", "void_reason")
EVENT_LABELS = {"insert": _("Created"), "update": _("Modified")}
FIELD_LABELS = {
    "code": _("Code"),
    "name": _("Name"),
    "description": _("Description"),
    "role": _("Role"),
    "voided_at": _("Voided at"),
    "void_reason": _("Void reason"),
}
PROJECT_LABEL = _("Project")
MEMBERSHIP_LABEL = _("Membership")
MAX_ENTRIES = 200


@dataclass
class HistoryEntry:
    occurred_at: datetime
    action: str
    subject: str
    changes: list[tuple[str, object, object]] = field(default_factory=list)
    user: User | None = None
    ip_address: str = ""
    request_id: str = ""
    reason: str = ""


def _changes(field_names, previous_event, event) -> list[tuple[str, object, object]]:
    changes = []
    for field_name in field_names:
        old_value = getattr(previous_event, field_name) if previous_event else None
        new_value = getattr(event, field_name)
        if old_value != new_value:
            changes.append((str(FIELD_LABELS[field_name]), old_value, new_value))
    return changes


def _entries_for(events, field_names, subject_for) -> list[HistoryEntry]:
    entries, previous_by_object = [], {}
    for event in events:
        metadata = event.pgh_context.metadata if event.pgh_context else {}
        entries.append(
            HistoryEntry(
                occurred_at=event.pgh_created_at,
                action=str(EVENT_LABELS.get(event.pgh_label, event.pgh_label)),
                subject=subject_for(event),
                changes=_changes(field_names, previous_by_object.get(event.pgh_obj_id), event),
                user=metadata.get("user"),
                ip_address=metadata.get("ip_address") or "",
                request_id=metadata.get("request_id") or "",
                reason=metadata.get("reason") or "",
            )
        )
        previous_by_object[event.pgh_obj_id] = event
    return entries


def project_history(project: Project) -> list[HistoryEntry]:
    """Newest first, project edits and membership changes together."""
    project_events = (
        ProjectEvent.objects.filter(pgh_obj=project)
        .select_related("pgh_context")
        .order_by("pgh_created_at", "pgh_id")
    )
    membership_events = (
        ProjectMembershipEvent.objects.filter(project=project)
        .select_related("pgh_context", "user")
        .order_by("pgh_created_at", "pgh_id")
    )
    entries = _entries_for(
        project_events, PROJECT_FIELDS, lambda event: str(PROJECT_LABEL)
    ) + _entries_for(
        membership_events,
        MEMBERSHIP_FIELDS,
        lambda event: f"{MEMBERSHIP_LABEL}: {event.user}",
    )
    entries.sort(key=lambda entry: entry.occurred_at, reverse=True)
    entries = entries[:MAX_ENTRIES]

    user_ids = {entry.user for entry in entries if entry.user}
    users_by_id = {str(user.pk): user for user in User.objects.filter(pk__in=user_ids)}
    for entry in entries:
        entry.user = users_by_id.get(entry.user)
    return entries
