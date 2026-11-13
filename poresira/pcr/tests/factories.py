# SPDX-License-Identifier: AGPL-3.0-or-later
import factory

from poresira.core.tests.factories import ProjectFactory
from poresira.pcr.models import (
    Assay,
    CyclingProgram,
    CyclingStep,
    MasterMix,
    MasterMixComponent,
    PcrReaction,
    PcrRun,
    Primer,
    PrimerDirection,
    StepStage,
)
from poresira.samples.tests.factories import SampleFactory


class PrimerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Primer

    name = factory.Sequence(lambda number: f"Primer-{number}")
    sequence = "ATGGCTGATAGCACTAAAGTCGA"
    direction = PrimerDirection.FORWARD


class CyclingProgramFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CyclingProgram

    name = factory.Sequence(lambda number: f"Program {number}")
    cycles = 30


class CyclingStepFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CyclingStep

    program = factory.SubFactory(CyclingProgramFactory)
    order = factory.Sequence(lambda number: number)
    stage = StepStage.CYCLE
    temperature_c = 94
    duration_seconds = 30


class MasterMixFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MasterMix

    name = factory.Sequence(lambda number: f"Mix {number}")
    reaction_volume_ul = 50


class MasterMixComponentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MasterMixComponent

    master_mix = factory.SubFactory(MasterMixFactory)
    name = factory.Sequence(lambda number: f"Ingredient {number}")
    volume_ul = 5


class AssayFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Assay

    name = factory.Sequence(lambda number: f"Assay {number}")
    target = "Sweet potato feathery mottle virus"
    forward_primer = factory.SubFactory(PrimerFactory)
    reverse_primer = factory.SubFactory(PrimerFactory, direction=PrimerDirection.REVERSE)
    cycling_program = factory.SubFactory(CyclingProgramFactory)


class PcrRunFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PcrRun

    project = factory.SubFactory(ProjectFactory)
    assay = factory.SubFactory(AssayFactory)


class PcrReactionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PcrReaction

    run = factory.SubFactory(PcrRunFactory)
    position = factory.Sequence(
        lambda number: f"A{number % 12 + 1}" if number < 12 else f"B{number % 12 + 1}"
    )
    sample = factory.SubFactory(SampleFactory, project=factory.SelfAttribute("..run.project"))
