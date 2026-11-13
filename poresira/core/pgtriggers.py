# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reusable PostgreSQL triggers for traceability rules."""

import pgtrigger

# pghistory stores the request or block context, reason included, in this session variable.
REASON_REQUIRED_SQL = """
    IF btrim(coalesce(
        nullif(current_setting('pghistory.context_metadata', true), '')::jsonb ->> 'reason', ''
    )) = '' THEN
        RAISE EXCEPTION 'A reason is required to change a critical field of %', TG_TABLE_NAME
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""


class RequireReason(pgtrigger.Trigger):
    """Reject an UPDATE that changes one of ``fields`` unless the audit context has a reason.

    Declared in a model's ``Meta.triggers``. It applies to ORM saves, ``QuerySet.update()``
    and raw SQL alike. Provide the reason with ``poresira.core.audit.with_reason``.
    """

    when = pgtrigger.Before
    operation = pgtrigger.Update

    def __init__(
        self,
        *,
        fields: list[str] | None = None,
        condition: pgtrigger.Condition | None = None,
        name: str = "require_reason",
    ):
        """Give ``fields`` (any change) or a custom ``condition`` for when a reason is needed."""
        super().__init__(
            name=name,
            condition=condition or pgtrigger.AnyChange(*fields),
            func=pgtrigger.Func(REASON_REQUIRED_SQL),
        )
