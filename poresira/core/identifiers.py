# SPDX-License-Identifier: AGPL-3.0-or-later
"""Human-readable identifiers such as ``WBF-S26-00123-K``.

Format: ``<site prefix>-<type letters><two-digit year>-<counter>-<check character>``.
The counter comes from one PostgreSQL sequence per type and year, so values are
never reused. The check character is a Damm check over a 32-symbol alphabet.
"""

import re

from django.conf import settings
from django.db import connection
from django.utils import timezone

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # no I, L, O, U: easy to misread
TYPE_CODE_PATTERN = re.compile(r"^[A-HJKMNP-TV-Z]{1,3}$")
SITE_PREFIX_PATTERN = re.compile(r"^[A-Z0-9]{1,8}$")
IDENTIFIER_PATTERN = re.compile(
    r"^(?P<prefix>[A-Z0-9]{1,8})-(?P<type_code>[A-HJKMNP-TV-Z]{1,3})(?P<year>\d{2})"
    r"-(?P<counter>\d{5,})-(?P<check>[0-9A-Z])$"
)
COUNTER_MIN_WIDTH = 5

# GF(2^5) reduced by x^5 + x^2 + 1. Interim = 2 * interim XOR symbol is a quasigroup
# whose adjacent-transposition property holds because (2 XOR 1) is invertible.
FIELD_REDUCTION = 0b100101
FIELD_SIZE = len(ALPHABET)


def _multiply_by_generator(value: int) -> int:
    shifted = value << 1
    return shifted ^ FIELD_REDUCTION if shifted >= FIELD_SIZE else shifted


def _damm_interim(payload: str) -> int:
    interim = 0
    for character in payload:
        interim = _multiply_by_generator(interim) ^ ALPHABET.index(character)
    return interim


def check_character(payload: str) -> str:
    """Check character to append to ``payload`` so the whole string verifies."""
    return ALPHABET[_multiply_by_generator(_damm_interim(payload))]


def has_valid_check_character(payload_with_check: str) -> bool:
    try:
        return _damm_interim(payload_with_check) == 0
    except ValueError:
        return False


def format_identifier(site_prefix: str, type_code: str, year: int, counter: int) -> str:
    if not SITE_PREFIX_PATTERN.match(site_prefix):
        raise ValueError(f"Invalid site prefix: {site_prefix!r}")
    if not TYPE_CODE_PATTERN.match(type_code):
        raise ValueError(f"Invalid type code: {type_code!r}")
    year_code = f"{year % 100:02d}"
    counter_code = f"{counter:0{COUNTER_MIN_WIDTH}d}"
    check = check_character(f"{type_code}{year_code}{counter_code}")
    return f"{site_prefix}-{type_code}{year_code}-{counter_code}-{check}"


def is_valid_identifier(identifier: str) -> bool:
    match = IDENTIFIER_PATTERN.match(identifier.upper())
    if match is None:
        return False
    payload = f"{match['type_code']}{match['year']}{match['counter']}{match['check']}"
    return has_valid_check_character(payload)


def next_identifier(type_code: str, *, site_prefix: str | None = None, now=None) -> str:
    """Allocate the next identifier for ``type_code`` in the current year (site time zone)."""
    if not TYPE_CODE_PATTERN.match(type_code):
        raise ValueError(f"Invalid type code: {type_code!r}")
    year = timezone.localtime(now or timezone.now()).year
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT poresira_next_identifier_counter(%s, %s)", [type_code, f"{year % 100:02d}"]
        )
        (counter,) = cursor.fetchone()
    return format_identifier(site_prefix or settings.PORESIRA_SITE_PREFIX, type_code, year, counter)
