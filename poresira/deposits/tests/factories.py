# SPDX-License-Identifier: AGPL-3.0-or-later
import factory
from django.utils import timezone

from poresira.core.tests.factories import ProjectFactory, UserFactory
from poresira.deposits.models import Deposit, DepositFile, DepositItem, PublicAccession
from poresira.samples.tests.factories import SampleFactory
from poresira.sequencing.tests.factories import SequencingRunFactory
from poresira.storage.models import FileKind
from poresira.storage.tests.factories import DataFileFactory


def sent_fields() -> dict:
    """What a deposit that left the draft always carries."""
    return {"submitted_at": timezone.now(), "submitted_by": UserFactory()}


def reads_file(item, kind=FileKind.FASTQ, **attributes):
    """A registered file of the project of ``item``, attached to its run."""
    return DataFileFactory(
        project=item.deposit.project, sequencing_run=item.sequencing_run, kind=kind, **attributes
    )


class DepositFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Deposit

    project = factory.SubFactory(ProjectFactory)
    title = factory.Sequence(lambda number: f"Sweetpotato viromes {number}")


class StudyItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DepositItem

    deposit = factory.SubFactory(DepositFactory)
    kind = "study"
    alias = factory.Sequence(lambda number: f"study_{number}")


class SampleItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DepositItem

    deposit = factory.SubFactory(DepositFactory)
    kind = "sample"
    alias = factory.Sequence(lambda number: f"sample_{number}")
    sample = factory.LazyAttribute(lambda item: SampleFactory(project=item.deposit.project))


class ReadsItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DepositItem

    deposit = factory.SubFactory(DepositFactory)
    kind = "reads"
    alias = factory.Sequence(lambda number: f"reads_{number}")
    sample_item = factory.SubFactory(SampleItemFactory, deposit=factory.SelfAttribute("..deposit"))
    sample = factory.LazyAttribute(lambda item: item.sample_item.sample)
    sequencing_run = factory.LazyAttribute(
        lambda item: SequencingRunFactory(library__project=item.deposit.project)
    )


class DepositFileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DepositFile

    item = factory.SubFactory(ReadsItemFactory)
    file = factory.LazyAttribute(
        lambda row: DataFileFactory(
            project=row.item.deposit.project,
            kind=FileKind.FASTQ,
            sequencing_run=row.item.sequencing_run,
        )
    )
    sha256 = factory.LazyAttribute(lambda row: row.file.sha256)


class PublicAccessionFactory(factory.django.DjangoModelFactory):
    """A BioSample of a field sample of the same project, unless another kind is given."""

    class Meta:
        model = PublicAccession

    project = factory.SubFactory(ProjectFactory)
    kind = "biosample"
    accession = factory.Sequence(lambda number: f"SAMN{57660000 + number}")
    sample = factory.LazyAttribute(
        lambda row: SampleFactory(project=row.project) if row.kind != "bioproject" else None
    )
