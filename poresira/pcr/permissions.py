# SPDX-License-Identifier: AGPL-3.0-or-later
from poresira.core.models import GlobalRole


def can_manage_reference_data(user) -> bool:
    """Primers, programs, recipes and assays are shared by all projects: platform staff own them."""
    return user.is_authenticated and (
        user.sees_all_projects or user.role == GlobalRole.PLATFORM_MANAGER
    )
