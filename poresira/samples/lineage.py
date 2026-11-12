# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ancestors and descendants of a sample, through recursive queries."""

from dataclasses import dataclass

from django.db import connection

from .models import Sample

ANCESTOR_IDS_SQL = """
    WITH RECURSIVE upward AS (
        SELECT id, parent_id, 0 AS depth FROM samples_sample WHERE id = %s
        UNION ALL
        SELECT parent.id, parent.parent_id, upward.depth + 1
        FROM samples_sample parent JOIN upward ON parent.id = upward.parent_id
    )
    SELECT id FROM upward WHERE depth > 0 ORDER BY depth DESC
"""

# The identifier path orders the rows depth first, siblings by identifier.
DESCENDANT_ROWS_SQL = """
    WITH RECURSIVE downward AS (
        SELECT id, 0 AS depth, ARRAY[identifier::text] AS path
        FROM samples_sample WHERE id = %s
        UNION ALL
        SELECT child.id, downward.depth + 1, downward.path || child.identifier::text
        FROM samples_sample child JOIN downward ON child.parent_id = downward.id
        WHERE %s OR child.voided_at IS NULL
    )
    SELECT id, depth FROM downward ORDER BY path
"""


@dataclass(frozen=True)
class LineageNode:
    sample: Sample
    depth: int


def ancestors(sample: Sample) -> list[Sample]:
    """Parent chain from the root field sample down to the direct parent."""
    with connection.cursor() as cursor:
        cursor.execute(ANCESTOR_IDS_SQL, [sample.pk])
        ordered_ids = [row[0] for row in cursor.fetchall()]
    samples_by_id = Sample.objects.in_bulk(ordered_ids)
    return [samples_by_id[sample_id] for sample_id in ordered_ids]


def descendants(sample: Sample, *, include_voided: bool = False) -> list[LineageNode]:
    """Everything derived from ``sample``, depth first, without ``sample`` itself."""
    return _tree_below(sample, include_voided)[1:]


def family_tree(sample: Sample, *, include_voided: bool = True) -> list[LineageNode]:
    """The whole family of ``sample``: its root field sample and everything below it."""
    chain = ancestors(sample)
    return _tree_below(chain[0] if chain else sample, include_voided)


def _tree_below(root: Sample, include_voided: bool) -> list[LineageNode]:
    with connection.cursor() as cursor:
        cursor.execute(DESCENDANT_ROWS_SQL, [root.pk, include_voided])
        rows = cursor.fetchall()
    samples_by_id = Sample.objects.in_bulk([sample_id for sample_id, _depth in rows])
    return [LineageNode(samples_by_id[sample_id], depth) for sample_id, depth in rows]
