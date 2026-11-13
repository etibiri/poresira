# SPDX-License-Identifier: AGPL-3.0-or-later
"""One adapter per sequencing platform: what is specific to it lives here, not in the services.

Oxford Nanopore is the first platform. Another one is added by writing an adapter and registering
it in ``ADAPTERS``; the models and services do not change.
"""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from .models import Chemistry, Platform

BARCODED_CHEMISTRIES = {
    Chemistry.NATIVE_BARCODING,
    Chemistry.RAPID_BARCODING,
    Chemistry.CDNA_PCR_BARCODING,
}


class PlatformAdapter:
    platform: str

    def check_kit(self, chemistry: str, barcode_count: int | None) -> None:
        raise NotImplementedError

    def parse_barcode(self, value) -> int:
        raise NotImplementedError

    def normalize_flow_cell_id(self, value: str) -> str:
        raise NotImplementedError

    def normalize_run_id(self, value: str) -> str:
        raise NotImplementedError


class NanoporeAdapter(PlatformAdapter):
    platform = Platform.NANOPORE
    BARCODE_PATTERN = re.compile(r"^(?:NB|BC|RB|barcode)?\s*0*(\d{1,3})$", re.IGNORECASE)
    FLOW_CELL_PATTERN = re.compile(r"^[A-Z0-9]{5,20}$")

    def check_kit(self, chemistry: str, barcode_count: int | None) -> None:
        if chemistry in BARCODED_CHEMISTRIES and not barcode_count:
            raise ValidationError({"barcode_count": _("A barcoding kit needs its barcode count.")})
        if chemistry == Chemistry.LIGATION and barcode_count:
            raise ValidationError(
                {"barcode_count": _("A kit without barcodes has no barcode count.")}
            )

    def parse_barcode(self, value) -> int:
        """``7``, ``07``, ``NB07``, ``barcode07`` and ``BC07`` all mean barcode 7."""
        match = self.BARCODE_PATTERN.match(str(value).strip())
        if not match or int(match.group(1)) < 1:
            raise ValidationError(
                _("%(value)s is not a barcode (use a number such as 7, NB07 or barcode07).")
                % {"value": value}
            )
        return int(match.group(1))

    def normalize_flow_cell_id(self, value: str) -> str:
        value = value.strip().upper()
        if not self.FLOW_CELL_PATTERN.match(value):
            raise ValidationError(
                {
                    "flow_cell_id": _(
                        "A flow cell ID has 5 to 20 letters and digits, such as PAW12345."
                    )
                }
            )
        return value

    def normalize_run_id(self, value: str) -> str:
        return value.strip().lower()


ADAPTERS: dict[str, PlatformAdapter] = {Platform.NANOPORE: NanoporeAdapter()}


def adapter_for(platform: str) -> PlatformAdapter:
    return ADAPTERS[platform]
