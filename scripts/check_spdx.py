# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail if a given source file lacks the AGPL SPDX header in its first lines."""

import sys
from pathlib import Path

SPDX_LINE = "SPDX-License-Identifier: AGPL-3.0-or-later"
HEADER_SEARCH_LINES = 3


def has_spdx_header(path: str) -> bool:
    first_lines = Path(path).read_text().splitlines()[:HEADER_SEARCH_LINES]
    return any(SPDX_LINE in line for line in first_lines)


def main(paths: list[str]) -> int:
    missing_header = [path for path in paths if not has_spdx_header(path)]
    for path in missing_header:
        print(f"Missing SPDX header: {path}")
    return 1 if missing_header else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
