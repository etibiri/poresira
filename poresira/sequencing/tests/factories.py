# SPDX-License-Identifier: AGPL-3.0-or-later
import factory

from poresira.core.tests.factories import ProjectFactory
from poresira.samples.models import SampleKind
from poresira.samples.tests.factories import DerivativeFactory, SampleFactory
from poresira.sequencing.models import (
    BarcodeResult,
    Chemistry,
    FlowCell,
    FlowCellType,
    Instrument,
    Library,
    LibraryKit,
    LibraryMember,
    SequencingRun,
)


class LibraryKitFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LibraryKit

    code = factory.Sequence(lambda number: f"SQK-TEST{number}")
    name = "Native Barcoding Kit 96 V14"
    chemistry = Chemistry.NATIVE_BARCODING
    barcode_count = 96


class FlowCellTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FlowCellType

    code = factory.Sequence(lambda number: f"FLO-TEST{number}")
    name = "MinION R10.4.1"
    pore = "R10.4.1"


class InstrumentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Instrument

    name = factory.Sequence(lambda number: f"MinION {number}")
    model = "MinION Mk1C"


class LibraryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Library

    project = factory.SubFactory(ProjectFactory)
    kit = factory.SubFactory(LibraryKitFactory)
    name = factory.Sequence(lambda number: f"Library {number}")


class LibraryMemberFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LibraryMember

    library = factory.SubFactory(LibraryFactory)
    sample = factory.LazyAttribute(
        lambda member: DerivativeFactory(
            kind=SampleKind.AMPLIFICATION_PRODUCT,
            parent=SampleFactory(project=member.library.project),
        )
    )
    barcode = factory.Sequence(lambda number: number % 90 + 1)


class FlowCellFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FlowCell

    flow_cell_id = factory.Sequence(lambda number: f"PAW{number:05d}")
    type = factory.SubFactory(FlowCellTypeFactory)


class SequencingRunFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SequencingRun

    library = factory.SubFactory(LibraryFactory, status="sealed")
    project = factory.SelfAttribute("library.project")
    flow_cell = factory.SubFactory(FlowCellFactory)


def run_of(library):
    """Seal the library (its members are in) and sequence it."""
    Library.objects.filter(pk=library.pk).update(status="sealed")
    library.refresh_from_db()
    return SequencingRunFactory(library=library)


class BarcodeResultFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BarcodeResult

    member = factory.SubFactory(LibraryMemberFactory)
    run = factory.LazyAttribute(lambda result: run_of(result.member.library))
    reads = 1000
