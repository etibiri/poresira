# SPDX-License-Identifier: AGPL-3.0-or-later
from poresira.core.models import Project, ProjectRole


def can_publish(user, project: Project) -> bool:
    """Sending data to a public archive is final: only a lead of the project, or an
    administrator who sees every project, may do it for real. A guest never does."""
    editable = Project.objects.editable_by(user).filter(pk=project.pk)
    if user.is_authenticated and user.sees_all_projects:
        return editable.exists()
    return editable.filter(
        memberships__user=user,
        memberships__role=ProjectRole.LEAD,
        memberships__voided_at__isnull=True,
    ).exists()
