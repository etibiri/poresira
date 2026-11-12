# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write permissions. Read access is ``Project.objects.visible_to``."""

from .models import Project


def can_edit_project(user, project: Project) -> bool:
    return Project.objects.editable_by(user).filter(pk=project.pk).exists()
