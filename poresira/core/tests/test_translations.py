# SPDX-License-Identifier: AGPL-3.0-or-later
import re
import subprocess
from pathlib import Path

from django.conf import settings

TRANSLATION_CALL = re.compile(r"""(?:\b_|gettext_lazy|trans|gettext)\(?\s*["']([^"']+)["']""")
PO_FILE = Path(settings.BASE_DIR) / "locale" / "fr" / "LC_MESSAGES" / "django.po"


def test_french_catalogue_has_no_untranslated_or_fuzzy_entry():
    statistics = subprocess.run(  # noqa: S603
        ["msgfmt", "--check", "--statistics", "-o", "/dev/null", str(PO_FILE)],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stderr

    assert not re.search(r"\d+ (untranslated|fuzzy)", statistics), statistics


def test_catalogue_is_in_sync_with_the_source_strings():
    """Fail when a new translatable string was added without running makemessages."""
    source_strings = set()
    for path in Path(settings.BASE_DIR, "poresira").rglob("*"):
        if "tests" in path.parts or path.suffix not in {".py", ".html"}:
            continue
        source_strings.update(
            re.findall(
                r"""(?:\b_|gettext_lazy|trans|gettext)\(?\s*["']([^"']+)["']""", path.read_text()
            )
        )
    catalogue = PO_FILE.read_text()

    missing = [text for text in source_strings if f'msgid "{text}"' not in catalogue]

    assert not missing, missing
