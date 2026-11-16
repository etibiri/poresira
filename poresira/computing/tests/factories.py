# SPDX-License-Identifier: AGPL-3.0-or-later
import factory
from django.utils import timezone

from poresira.computing.models import ComputeJob, JobInput, JobKind, JobOutput
from poresira.core.tests.factories import ProjectFactory
from poresira.sequencing.tests.factories import SequencingRunFactory
from poresira.storage.tests.factories import DataFileFactory

SUBMITTED = {"submitted_at": factory.LazyFunction(timezone.now), "script": "#!/bin/bash\n"}


class ComputeJobFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ComputeJob

    project = factory.SubFactory(ProjectFactory)
    sequencing_run = factory.LazyAttribute(
        lambda job: SequencingRunFactory(library__project=job.project)
    )
    kind = JobKind.BASECALLING
    parameters = factory.LazyFunction(lambda: {"model": "sup"})


class JobInputFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = JobInput

    job = factory.SubFactory(ComputeJobFactory)
    file = factory.LazyAttribute(lambda row: DataFileFactory(project=row.job.project))
    sha256 = factory.LazyAttribute(lambda row: row.file.sha256)


class JobOutputFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = JobOutput

    job = factory.SubFactory(ComputeJobFactory, status="running", **SUBMITTED)
    file = factory.LazyAttribute(lambda row: DataFileFactory(project=row.job.project))
