# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory


def tracked():
    """Fresh event definitions for each model: pghistory binds them to the model it decorates."""
    return (
        pghistory.InsertEvent(),
        pghistory.UpdateEvent(condition=pghistory.AnyChange(exclude_auto=True)),
    )
