# SPDX-License-Identifier: AGPL-3.0-or-later
"""UUIDv7 (RFC 9562) generator; Python 3.12 has no uuid.uuid7 and PostgreSQL 16 no uuidv7()."""

import os
import time
from uuid import UUID

TIMESTAMP_BITS = 48
RANDOM_A_BITS = 12
RANDOM_B_BITS = 62
RANDOM_BYTES = 10


def uuid7() -> UUID:
    """Time-ordered UUID. Not strictly monotonic within the same millisecond."""
    timestamp_ms = time.time_ns() // 1_000_000
    random_bits = int.from_bytes(os.urandom(RANDOM_BYTES), "big")
    random_a = random_bits >> (RANDOM_BYTES * 8 - RANDOM_A_BITS)
    random_b = random_bits & ((1 << RANDOM_B_BITS) - 1)

    value = (timestamp_ms & ((1 << TIMESTAMP_BITS) - 1)) << 80
    value |= 0x7 << 76
    value |= random_a << 64
    value |= 0b10 << 62
    value |= random_b
    return UUID(int=value)
