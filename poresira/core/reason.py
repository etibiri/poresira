# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scoped reasons for audited changes.

pghistory keeps its context in a session variable that lives until the end of the current
transaction, and merges new metadata into an already active context for good. Used as is,
a reason given for one change would silently cover later changes in the same transaction or
request. ``with_reason`` removes the reason again when its block ends.
"""

from contextlib import contextmanager

import pghistory
import pghistory.runtime
from django.db import connection
from django.utils.translation import gettext_lazy as _

_NO_PREVIOUS_REASON = object()


def _clear_session_context() -> None:
    """Forget pghistory's variables for the rest of the open transaction."""
    if connection.in_atomic_block:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('pghistory.context_id', '', true), "
                "set_config('pghistory.context_metadata', '', true)"
            )


@contextmanager
def with_reason(reason: str):
    """Attach a mandatory reason to every audit entry written inside the block, and only there."""
    reason = reason.strip()
    if not reason:
        raise ValueError(_("A reason is required for this change."))

    active_context = getattr(pghistory.runtime._tracker, "value", None)
    if active_context is None:
        with pghistory.context(reason=reason):
            yield
        _clear_session_context()
        return

    previous_reason = active_context.metadata.get("reason", _NO_PREVIOUS_REASON)
    active_context.metadata["reason"] = reason
    try:
        yield
    finally:
        if previous_reason is _NO_PREVIOUS_REASON:
            active_context.metadata.pop("reason", None)
        else:
            active_context.metadata["reason"] = previous_reason
