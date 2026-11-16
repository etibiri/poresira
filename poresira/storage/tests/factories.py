# SPDX-License-Identifier: AGPL-3.0-or-later
import factory

from poresira.core.tests.factories import ProjectFactory
from poresira.sequencing.tests.factories import SequencingRunFactory
from poresira.storage.models import BackendKind, DataFile, FileKind, FixityCheck, StorageBackend


class StorageBackendFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StorageBackend

    name = factory.Sequence(lambda number: f"Disk {number}")
    kind = BackendKind.LOCAL
    root = factory.Sequence(lambda number: f"/data/disk{number}")


class DataFileFactory(factory.django.DjangoModelFactory):
    """Linked to a sequencing run; for another link pass ``sequencing_run=None``."""

    class Meta:
        model = DataFile

    project = factory.SubFactory(ProjectFactory)
    backend = factory.SubFactory(StorageBackendFactory)
    path = factory.Sequence(lambda number: f"run/reads_{number}.fastq.gz")
    kind = FileKind.FASTQ
    size_bytes = 1024
    sha256 = factory.Sequence(lambda number: f"{number:064x}")
    md5 = factory.Sequence(lambda number: f"{number:032x}")
    sequencing_run = factory.LazyAttribute(
        lambda file: SequencingRunFactory(library__project=file.project)
    )


class FixityCheckFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FixityCheck

    file = factory.SubFactory(DataFileFactory)
    outcome = "intact"
