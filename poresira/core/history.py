# SPDX-License-Identifier: AGPL-3.0-or-later
"""Readable history built from pghistory event tables, for any audited model."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from .models import Project, ProjectEvent, ProjectMembershipEvent, User

EVENT_LABELS = {"insert": _("Created"), "update": _("Modified")}
MAX_ENTRIES = 200


@dataclass
class HistoryEntry:
    occurred_at: datetime
    event_id: int
    action: str
    subject: str
    changes: list[tuple[str, object, object]] = field(default_factory=list)
    user: User | None = None
    ip_address: str = ""
    request_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class HistorySource:
    """Event rows of one audited model, and how to show them.

    ``events`` must be ordered by ``pgh_created_at, pgh_id``. ``labels`` overrides the field
    labels, which otherwise come from the event model's ``verbose_name``.
    """

    events: QuerySet
    field_names: tuple[str, ...]
    subject_for: Callable[[object], str]
    labels: dict[str, str] = field(default_factory=dict)


def _field_label(source: HistorySource, field_name: str) -> str:
    if field_name in source.labels:
        return str(source.labels[field_name])
    return str(source.events.model._meta.get_field(field_name).verbose_name).capitalize()


def _entries_for(source: HistorySource) -> list[HistoryEntry]:
    entries, previous_by_object = [], {}
    for event in source.events.select_related("pgh_context"):
        previous_event = previous_by_object.get(event.pgh_obj_id)
        changes = [
            (_field_label(source, field_name), getattr(previous_event, field_name, None), new_value)
            for field_name in source.field_names
            if (new_value := getattr(event, field_name))
            != (getattr(previous_event, field_name) if previous_event else None)
        ]
        metadata = event.pgh_context.metadata if event.pgh_context else {}
        entries.append(
            HistoryEntry(
                occurred_at=event.pgh_created_at,
                event_id=event.pgh_id,
                action=str(EVENT_LABELS.get(event.pgh_label, event.pgh_label)),
                subject=source.subject_for(event),
                changes=changes,
                user=metadata.get("user"),
                ip_address=metadata.get("ip_address") or "",
                request_id=metadata.get("request_id") or "",
                reason=metadata.get("reason") or "",
            )
        )
        previous_by_object[event.pgh_obj_id] = event
    return entries


def build_history(sources: Iterable[HistorySource], limit: int = MAX_ENTRIES) -> list[HistoryEntry]:
    """Newest first, all sources merged, with users resolved in one query.

    Events of one transaction share a timestamp, so the event id orders them within a source.
    """
    entries = [entry for source in sources for entry in _entries_for(source)]
    entries.sort(key=lambda entry: (entry.occurred_at, entry.event_id), reverse=True)
    entries = entries[:limit]

    user_ids = {entry.user for entry in entries if entry.user}
    users_by_id = {str(user.pk): user for user in User.objects.filter(pk__in=user_ids)}
    for entry in entries:
        entry.user = users_by_id.get(entry.user)
    return entries


PROJECT_LABEL = _("Project")
MEMBERSHIP_LABEL = _("Membership")
VOID_FIELDS = ("voided_at", "void_reason")
PROJECT_LABELS = {
    "code": _("Code"),
    "name": _("Name"),
    "description": _("Description"),
    "role": _("Role"),
    "voided_at": _("Voided at"),
    "void_reason": _("Void reason"),
}


def project_history(project: Project) -> list[HistoryEntry]:
    """Project edits and membership changes together."""
    return build_history(
        [
            HistorySource(
                events=ProjectEvent.objects.filter(pgh_obj=project).order_by(
                    "pgh_created_at", "pgh_id"
                ),
                field_names=("code", "name", "description", *VOID_FIELDS),
                subject_for=lambda event: str(PROJECT_LABEL),
                labels=PROJECT_LABELS,
            ),
            HistorySource(
                events=ProjectMembershipEvent.objects.filter(project=project)
                .select_related("user")
                .order_by("pgh_created_at", "pgh_id"),
                field_names=("role", *VOID_FIELDS),
                subject_for=lambda event: f"{MEMBERSHIP_LABEL}: {event.user}",
                labels=PROJECT_LABELS,
            ),
        ]
    )
