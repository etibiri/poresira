# SPDX-License-Identifier: AGPL-3.0-or-later
from poresira.core.models import Project
from poresira.pcr.permissions import can_manage_reference_data

__all__ = ["can_manage_reference_data", "can_register_flow_cells"]


def can_register_flow_cells(user) -> bool:
    """Flow cells belong to no project: anyone who may edit some project can register one."""
    return user.is_authenticated and (
        can_manage_reference_data(user) or Project.objects.editable_by(user).exists()
    )
