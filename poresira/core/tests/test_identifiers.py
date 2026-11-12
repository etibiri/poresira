# SPDX-License-Identifier: AGPL-3.0-or-later
import datetime
import threading

import pytest
from django.db import DatabaseError, connection, connections, transaction

from poresira.core.identifiers import (
    ALPHABET,
    check_character,
    format_identifier,
    has_valid_check_character,
    is_valid_identifier,
    next_identifier,
)

SAMPLE_PAYLOADS = ["S2600123", "P2600001", "SQ2699999", "R25000007", "S26123456789"]


def with_check(payload: str) -> str:
    return payload + check_character(payload)


@pytest.mark.parametrize("payload", SAMPLE_PAYLOADS)
def test_check_character_validates_its_own_payload(payload):
    assert has_valid_check_character(with_check(payload))


@pytest.mark.parametrize("payload", SAMPLE_PAYLOADS)
def test_any_single_character_typo_is_detected(payload):
    valid = with_check(payload)
    for position, original in enumerate(valid):
        for replacement in ALPHABET:
            if replacement == original:
                continue
            typo = valid[:position] + replacement + valid[position + 1 :]
            assert not has_valid_check_character(typo), (valid, typo)


@pytest.mark.parametrize("payload", SAMPLE_PAYLOADS)
def test_any_adjacent_transposition_is_detected(payload):
    valid = with_check(payload)
    for position in range(len(valid) - 1):
        first, second = valid[position], valid[position + 1]
        if first == second:
            continue
        swapped = valid[:position] + second + first + valid[position + 2 :]
        assert not has_valid_check_character(swapped), (valid, swapped)


def test_characters_outside_the_alphabet_are_invalid():
    assert not has_valid_check_character("S26OOO1I")


def test_format_matches_documented_shape():
    identifier = format_identifier("WBF", "S", 2026, 123)
    assert identifier.startswith("WBF-S26-00123-")
    assert is_valid_identifier(identifier)


def test_format_rejects_ambiguous_type_letters_and_bad_prefix():
    with pytest.raises(ValueError, match="type code"):
        format_identifier("WBF", "O", 2026, 1)
    with pytest.raises(ValueError, match="site prefix"):
        format_identifier("wbf-x", "S", 2026, 1)


def test_counter_grows_past_five_digits():
    identifier = format_identifier("WBF", "S", 2026, 1234567)
    assert "-1234567-" in identifier
    assert is_valid_identifier(identifier)


def test_identifier_with_wrong_check_or_shape_is_invalid():
    identifier = format_identifier("WBF", "S", 2026, 123)
    wrong_check = identifier[:-1] + ("0" if identifier[-1] != "0" else "1")
    assert not is_valid_identifier(wrong_check)
    assert not is_valid_identifier("not-an-identifier")
    assert is_valid_identifier(identifier.lower())


@pytest.fixture
def drop_sequences_afterwards():
    created = []
    yield created
    with connection.cursor() as cursor:
        for sequence_name in created:
            cursor.execute(f"DROP SEQUENCE IF EXISTS {sequence_name}")


@pytest.mark.django_db
def test_counters_are_sequential_per_type_and_year():
    now = datetime.datetime(2026, 3, 1, tzinfo=datetime.UTC)
    first_sample = next_identifier("T", site_prefix="WBF", now=now)
    second_sample = next_identifier("T", site_prefix="WBF", now=now)
    first_extract = next_identifier("TX", site_prefix="WBF", now=now)
    next_year = next_identifier("T", site_prefix="WBF", now=now.replace(year=2027))

    assert first_sample.startswith("WBF-T26-00001-")
    assert second_sample.startswith("WBF-T26-00002-")
    assert first_extract.startswith("WBF-TX26-00001-")
    assert next_year.startswith("WBF-T27-00001-")
    assert all(
        is_valid_identifier(value)
        for value in (first_sample, second_sample, first_extract, next_year)
    )


@pytest.mark.django_db
def test_year_follows_the_configured_time_zone(settings):
    just_before_new_year_utc = datetime.datetime(2026, 12, 31, 23, 30, tzinfo=datetime.UTC)
    settings.TIME_ZONE = "UTC"
    assert next_identifier("TZ", now=just_before_new_year_utc).split("-")[1] == "TZ26"
    settings.TIME_ZONE = "Pacific/Auckland"
    assert next_identifier("TZ", now=just_before_new_year_utc).split("-")[1] == "TZ27"


@pytest.mark.django_db
def test_site_prefix_defaults_to_setting(settings):
    settings.PORESIRA_SITE_PREFIX = "ABC"
    assert next_identifier("TP").startswith("ABC-")


@pytest.mark.django_db
def test_rolled_back_transaction_does_not_free_its_number():
    first = next_identifier("TR")
    with pytest.raises(RuntimeError), transaction.atomic():
        burned = next_identifier("TR")
        raise RuntimeError
    after_rollback = next_identifier("TR")

    counters = [int(value.split("-")[2]) for value in (first, burned, after_rollback)]
    assert counters[0] < counters[1] < counters[2]


@pytest.mark.django_db
def test_invalid_type_code_is_rejected_by_python_and_by_database():
    with pytest.raises(ValueError, match="type code"):
        next_identifier("ILO")
    with pytest.raises(DatabaseError), connection.cursor() as cursor:
        cursor.execute("SELECT poresira_next_identifier_counter('bad type', '26')")


@pytest.mark.django_db(transaction=True)
def test_concurrent_allocation_never_duplicates(drop_sequences_afterwards):
    drop_sequences_afterwards.append(f"poresira_id_tc_{datetime.date.today().year % 100:02d}")
    thread_count, identifiers_per_thread = 8, 25
    allocated: list[str] = []
    errors: list[Exception] = []
    start_together = threading.Barrier(thread_count)

    def allocate():
        try:
            start_together.wait()
            allocated.extend(next_identifier("TC") for _ in range(identifiers_per_thread))
        except Exception as error:
            errors.append(error)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=allocate) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(allocated) == thread_count * identifiers_per_thread
    assert len(set(allocated)) == len(allocated)
