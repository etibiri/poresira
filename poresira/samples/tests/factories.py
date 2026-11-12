# SPDX-License-Identifier: AGPL-3.0-or-later
import factory

from poresira.core.tests.factories import ProjectFactory, UserFactory
from poresira.samples.models import (
    CollectionEvent,
    QcMeasurement,
    QcMethod,
    Sample,
    SampleKind,
    StorageLocation,
    StorageLocationKind,
)


class StorageLocationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StorageLocation

    name = factory.Sequence(lambda number: f"Freezer {number}")
    kind = StorageLocationKind.FREEZER


class CollectionEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CollectionEvent

    project = factory.SubFactory(ProjectFactory)
    locality = "Tiébélé"


class SampleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Sample

    project = factory.SubFactory(ProjectFactory)
    collection_event = factory.SubFactory(
        CollectionEventFactory, project=factory.SelfAttribute("..project")
    )
    kind = SampleKind.FIELD_SAMPLE
    label = factory.Sequence(lambda number: f"BFA{number}")


class DerivativeFactory(SampleFactory):
    kind = SampleKind.DNA_EXTRACT
    parent = factory.SubFactory(SampleFactory)
    project = factory.SelfAttribute("parent.project")
    collection_event = factory.SelfAttribute("parent.collection_event")
    label = ""


class QcMeasurementFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = QcMeasurement

    sample = factory.SubFactory(DerivativeFactory)
    method = QcMethod.NANODROP
    concentration_ng_ul = 42
    measured_by = factory.SubFactory(UserFactory)
