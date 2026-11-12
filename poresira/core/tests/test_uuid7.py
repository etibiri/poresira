# SPDX-License-Identifier: AGPL-3.0-or-later
import time
from unittest import mock

from poresira.core.uuid7 import uuid7


def test_version_and_variant_bits():
    generated = uuid7()
    assert generated.version == 7
    assert generated.variant == "specified in RFC 4122"


def test_embeds_millisecond_timestamp():
    before_ms = time.time_ns() // 1_000_000
    timestamp_ms = uuid7().int >> 80
    assert before_ms <= timestamp_ms <= time.time_ns() // 1_000_000


def test_sorts_by_creation_time():
    with mock.patch("time.time_ns", side_effect=[1_000_000_000, 2_000_000_000]):
        earlier, later = uuid7(), uuid7()
    assert earlier < later


def test_unique():
    assert len({uuid7() for _ in range(1000)}) == 1000
