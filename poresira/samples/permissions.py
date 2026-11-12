# SPDX-License-Identifier: AGPL-3.0-or-later
from poresira.core.models import GlobalRole


def can_manage_storage(user) -> bool:
    """Storage locations are shared by every project, so only platform staff shape the tree."""
    return user.is_authenticated and (
        user.sees_all_projects or user.role == GlobalRole.PLATFORM_MANAGER
    )
