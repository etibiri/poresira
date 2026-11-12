# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
import pytest
from django.db import IntegrityError, connection, transaction

from poresira.core.audit import with_reason
from poresira.core.tests.probe_models import ReasonProbe

pytestmark = pytest.mark.django_db


@pytest.fixture
def probe_row():
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(ReasonProbe)
    pgtrigger.install("core.ReasonProbe:require_reason")
    return ReasonProbe.objects.create(amount=1, note="first")


def assert_rejected_and_unchanged(action):
    assert_rejected_and_unchanged_at(action, expected_amount=1)


def assert_rejected_and_unchanged_at(action, expected_amount):
    with pytest.raises(IntegrityError, match="reason is required"), transaction.atomic():
        action()
    assert ReasonProbe.objects.get().amount == expected_amount


def test_inserting_needs_no_reason(probe_row):
    assert ReasonProbe.objects.count() == 1


def test_changing_a_non_critical_field_needs_no_reason(probe_row):
    probe_row.note = "second"
    probe_row.save()

    assert ReasonProbe.objects.get().note == "second"


def test_changing_a_critical_field_without_reason_is_rejected(probe_row):
    probe_row.amount = 2

    assert_rejected_and_unchanged(probe_row.save)


def test_queryset_update_and_raw_sql_are_rejected_too(probe_row):
    assert_rejected_and_unchanged(lambda: ReasonProbe.objects.update(amount=2))

    def raw_update():
        with connection.cursor() as cursor:
            cursor.execute("UPDATE core_reasonprobe SET amount = 2")

    assert_rejected_and_unchanged(raw_update)


@pytest.mark.parametrize("context_metadata", [{}, {"reason": ""}, {"reason": "   "}])
def test_context_without_a_real_reason_is_rejected(probe_row, context_metadata):
    probe_row.amount = 2

    with pghistory.context(**context_metadata):
        assert_rejected_and_unchanged(probe_row.save)


def test_change_with_a_reason_is_accepted(probe_row):
    probe_row.amount = 2

    with with_reason("Recounted after re-weighing"):
        probe_row.save()

    assert ReasonProbe.objects.get().amount == 2


def test_with_reason_refuses_a_blank_reason_before_touching_the_database():
    with pytest.raises(ValueError, match="reason"), with_reason("   "):
        pytest.fail("the block must not run")


class TestReasonIsScoped:
    """pghistory's context outlives its block inside a transaction; with_reason must not."""

    def test_reason_does_not_cover_later_changes_in_the_same_transaction(self, probe_row):
        probe_row.amount = 2
        with with_reason("Recount"):
            probe_row.save()
        probe_row.amount = 3

        assert_rejected_and_unchanged_at(probe_row.save, expected_amount=2)

    def test_reason_given_inside_a_request_context_is_removed_afterwards(self, probe_row):
        probe_row.amount = 2
        with pghistory.context(user="someone"):
            with with_reason("Recount"):
                probe_row.save()
            probe_row.amount = 3

            assert_rejected_and_unchanged_at(probe_row.save, expected_amount=2)

    def test_a_reason_already_in_the_context_comes_back_after_the_block(self, probe_row):
        with pghistory.context(reason="Request-level reason") as context:
            with with_reason("Inner reason"):
                assert context.metadata["reason"] == "Inner reason"

            assert context.metadata["reason"] == "Request-level reason"
            probe_row.amount = 2
            probe_row.save()

    def test_a_request_level_reason_covers_the_whole_request(self, probe_row):
        with pghistory.context(reason="Typo fixed from the web form"):
            probe_row.amount = 2
            probe_row.save()
            probe_row.amount = 3
            probe_row.save()

        assert ReasonProbe.objects.get().amount == 3
