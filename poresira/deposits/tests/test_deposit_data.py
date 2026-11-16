# SPDX-License-Identifier: AGPL-3.0-or-later
"""The records of a library on a completed run, ready to be deposited."""

from decimal import Decimal

import pytest

from poresira.core.tests.factories import ProjectFactory
from poresira.samples.models import SampleKind
from poresira.samples.tests.factories import (
    CollectionEventFactory,
    DerivativeFactory,
    SampleFactory,
)
from poresira.sequencing.models import Library, LibraryStatus, RunStatus, SequencingRun
from poresira.sequencing.tests.factories import (
    InstrumentFactory,
    LibraryFactory,
    LibraryKitFactory,
    LibraryMemberFactory,
    SequencingRunFactory,
)
from poresira.storage.models import FileKind
from poresira.storage.tests.factories import DataFileFactory


@pytest.fixture
def project():
    return ProjectFactory()


def field_sample(project, event_attributes=None, **attributes):
    event = CollectionEventFactory(
        **{
            "project": project,
            "collected_on": "2016-04-16",
            "country": "Burkina Faso",
            "region": "Centre-Sud",
            "province": "Nahouri",
            "locality": "Tiebele",
            "latitude": Decimal("11.08"),
            "longitude": Decimal("-0.90"),
            **(event_attributes or {}),
        }
    )
    defaults = {
        "project": project,
        "collection_event": event,
        "label": "Tie_BFA316",
        "host_taxon": "Ipomoea batatas",
        "host_tax_id": 4120,
        "symptom_score": 7,
    }
    return SampleFactory(**{**defaults, **attributes})


def sequenced(
    project,
    barcodes=(1, 2),
    *,
    completed=True,
    shared_parent=False,
    sample_attributes=None,
    event_attributes=None,
    **run_attributes,
):
    """A run of a barcoded library, each member a RCA product of its own field sample."""
    library = LibraryFactory(
        project=project,
        kit=LibraryKitFactory(),
        name="RCA library",
        reconstructed=None in barcodes,
    )
    parent = field_sample(
        project, label="Tie_BFA0", event_attributes=event_attributes, **(sample_attributes or {})
    )
    members = []
    for barcode in barcodes:
        if not shared_parent:
            parent = field_sample(
                project,
                label=f"Tie_BFA{barcode}",
                event_attributes=event_attributes,
                **(sample_attributes or {}),
            )
        product = DerivativeFactory(
            kind=SampleKind.AMPLIFICATION_PRODUCT,
            parent=parent,
            label=f"{parent.label}_{barcode}" if shared_parent else parent.label,
        )
        members.append(LibraryMemberFactory(library=library, sample=product, barcode=barcode))
    Library.objects.filter(pk=library.pk).update(status=LibraryStatus.SEALED)
    library.refresh_from_db()
    attributes = {"instrument": InstrumentFactory(model="MinION Mk1C"), **run_attributes}
    run = SequencingRunFactory(library=library, **attributes)
    if completed:
        SequencingRun.objects.filter(pk=run.pk).update(
            status=RunStatus.COMPLETED, basecaller="Dorado", basecaller_version="0.9.0"
        )
        run.refresh_from_db()
    return run, members


def reads_file(project, run, path, kind=FileKind.FASTQ, **attributes):
    return DataFileFactory(project=project, sequencing_run=run, path=path, kind=kind, **attributes)
