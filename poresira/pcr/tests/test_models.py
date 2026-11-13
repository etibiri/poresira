# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction

from poresira.core.audit import verify_audit_chain, with_reason
from poresira.core.identifiers import TYPE_CODE_PATTERN, is_valid_identifier
from poresira.core.models import AuditChainLink
from poresira.core.tests.factories import ProjectFactory, UserFactory
from poresira.core.voiding import HardDeleteForbiddenError
from poresira.pcr.models import (
    PCR_RUN_TYPE_CODE,
    Container,
    PcrReaction,
    PcrRun,
    Primer,
    Role,
    RunStatus,
)
from poresira.pcr.tests.factories import (
    AssayFactory,
    CyclingProgramFactory,
    CyclingStepFactory,
    MasterMixComponentFactory,
    MasterMixFactory,
    PcrReactionFactory,
    PcrRunFactory,
    PrimerFactory,
)
from poresira.samples.models import SampleKind
from poresira.samples.tests.factories import DerivativeFactory, SampleFactory

pytestmark = pytest.mark.django_db


def refuses(error, action):
    with pytest.raises(error), transaction.atomic():
        action()


class TestPrimer:
    def test_degenerate_bases_are_accepted(self):
        primer = PrimerFactory(sequence="CCCCKGTGCGWRAATCCAT")  # SPG1 of Li et al. 2004

        primer.full_clean()
        assert primer.length == 19

    @pytest.mark.parametrize("sequence", ["atgc", "ATGX", "AT GC", "AUGC"])
    def test_anything_but_upper_case_iupac_dna_is_refused(self, sequence):
        with pytest.raises(ValidationError):
            Primer(name="bad", sequence=sequence, direction="forward").full_clean()
        refuses(IntegrityError, lambda: PrimerFactory(sequence=sequence))

    def test_the_sequence_cannot_be_rewritten(self):
        primer = PrimerFactory()
        primer.sequence = "ACGT"

        refuses(DatabaseError, primer.save)

    def test_an_active_name_is_unique(self):
        PrimerFactory(name="CP-F")

        refuses(IntegrityError, lambda: PrimerFactory(name="CP-F"))


class TestProgramAndMix:
    def test_steps_are_fixed_and_cannot_be_deleted(self):
        step = CyclingStepFactory()
        step.temperature_c = 95
        refuses(DatabaseError, step.save)
        refuses(DatabaseError, lambda: type(step).objects.filter(pk=step.pk).delete())

    def test_step_order_is_unique_within_a_program(self):
        step = CyclingStepFactory(order=1)

        refuses(IntegrityError, lambda: CyclingStepFactory(program=step.program, order=1))

    def test_a_step_needs_a_plausible_temperature_and_duration(self):
        refuses(IntegrityError, lambda: CyclingStepFactory(temperature_c=120))
        refuses(IntegrityError, lambda: CyclingStepFactory(duration_seconds=0))

    def test_the_number_of_cycles_is_fixed_and_positive(self):
        refuses(IntegrityError, lambda: CyclingProgramFactory(cycles=0))
        program = CyclingProgramFactory()
        program.cycles = 35
        refuses(DatabaseError, program.save)

    def test_recipe_components_are_fixed_and_cannot_be_deleted(self):
        component = MasterMixComponentFactory()
        component.volume_ul = 6
        refuses(DatabaseError, component.save)
        refuses(DatabaseError, lambda: type(component).objects.filter(pk=component.pk).delete())

    def test_an_ingredient_appears_once_per_recipe(self):
        component = MasterMixComponentFactory(name="dNTP")

        refuses(
            IntegrityError,
            lambda: MasterMixComponentFactory(master_mix=component.master_mix, name="dNTP"),
        )

    def test_reference_data_is_voided_with_a_reason_never_deleted(self):
        mix = MasterMixFactory()

        with pytest.raises(HardDeleteForbiddenError):
            mix.delete()
        mix.void(user=UserFactory(), reason="Superseded by a new recipe")
        assert mix.is_voided


class TestAssay:
    def test_the_design_cannot_change_after_creation(self):
        assay = AssayFactory()
        assay.cycling_program = CyclingProgramFactory()

        refuses(DatabaseError, assay.save)

    def test_notes_may_still_be_edited(self):
        assay = AssayFactory()
        assay.notes = "Works best at 58 °C"
        assay.save()

        assert assay.events.filter(pgh_label="update").count() == 1


class TestRun:
    def test_identifier_uses_its_own_valid_type_letter(self):
        run = PcrRunFactory()

        assert TYPE_CODE_PATTERN.match(PCR_RUN_TYPE_CODE)
        assert is_valid_identifier(run.identifier)
        assert "-T" in run.identifier

    def test_tubes_need_a_count_and_plates_must_not_have_one(self):
        refuses(IntegrityError, lambda: PcrRunFactory(container=Container.TUBES))
        refuses(IntegrityError, lambda: PcrRunFactory(container=Container.PLATE_96, tube_count=8))
        refuses(IntegrityError, lambda: PcrRunFactory(container=Container.TUBES, tube_count=97))

        run = PcrRunFactory(container=Container.TUBES, tube_count=8)

        assert run.tube_count == 8

    def test_what_a_run_is_cannot_change(self):
        run = PcrRunFactory()
        for field, value in {
            "assay": AssayFactory(),
            "project": ProjectFactory(),
            "container": Container.TUBES,
        }.items():
            setattr(run, field, value)
            refuses(DatabaseError, run.save)
            run.refresh_from_db()

    def test_reopening_a_closed_run_needs_a_reason(self):
        run = PcrRunFactory(status=RunStatus.CLOSED)
        run.status = RunStatus.PERFORMED
        refuses(DatabaseError, run.save)

        with with_reason("A band was misread"):
            run.save()

        run.refresh_from_db()
        assert run.status == RunStatus.PERFORMED

    def test_closing_a_run_needs_no_reason(self):
        run = PcrRunFactory(status=RunStatus.PERFORMED)
        run.status = RunStatus.CLOSED
        run.save()


class TestReaction:
    def test_a_position_holds_one_active_reaction_per_run(self):
        reaction = PcrReactionFactory(position="A1")

        refuses(
            IntegrityError,
            lambda: PcrReactionFactory(run=reaction.run, position="A1", sample=reaction.sample),
        )
        reaction.void(user=UserFactory(), reason="Wrong well")
        PcrReactionFactory(run=reaction.run, position="A1", sample=reaction.sample)

    def test_a_sample_reaction_needs_a_template_but_controls_do_not(self):
        run = PcrRunFactory()

        refuses(
            IntegrityError,
            lambda: PcrReaction.objects.create(run=run, position="A1", role=Role.SAMPLE),
        )
        PcrReaction.objects.create(run=run, position="H12", role=Role.NEGATIVE_CONTROL)

    def test_the_template_must_belong_to_the_project_of_the_run(self):
        run = PcrRunFactory()
        other_project_sample = SampleFactory()

        refuses(
            DatabaseError,
            lambda: PcrReaction.objects.create(run=run, position="A1", sample=other_project_sample),
        )

    def test_a_living_plant_cannot_be_a_template_but_its_extract_can(self):
        run = PcrRunFactory()
        field = SampleFactory(project=run.project)
        plant = DerivativeFactory(parent=field, kind=SampleKind.LIVING_PLANT)
        extract = DerivativeFactory(parent=plant, kind=SampleKind.DNA_EXTRACT)

        refuses(
            DatabaseError,
            lambda: PcrReaction.objects.create(run=run, position="A1", sample=plant),
        )
        PcrReaction.objects.create(run=run, position="A2", sample=extract)

    def test_a_reaction_cannot_move_to_another_well_or_run(self):
        reaction = PcrReactionFactory(position="A1")
        reaction.position = "A2"

        refuses(DatabaseError, reaction.save)

    def test_a_result_is_free_to_enter_until_the_run_is_closed(self):
        reaction = PcrReactionFactory(run=PcrRunFactory(status=RunStatus.PERFORMED))
        reaction.result = "positive"
        reaction.band_size_bp = 700
        reaction.save()

    def test_a_result_of_a_closed_run_changes_only_with_a_reason(self):
        reaction = PcrReactionFactory(run=PcrRunFactory(status=RunStatus.CLOSED))
        reaction.result = "positive"
        refuses(DatabaseError, reaction.save)

        with with_reason("Band checked again on a new gel"):
            reaction.save()

        assert reaction.events.filter(pgh_label="update").count() == 1

    def test_ct_and_band_size_must_be_positive(self):
        refuses(IntegrityError, lambda: PcrReactionFactory(ct=0))
        refuses(IntegrityError, lambda: PcrReactionFactory(band_size_bp=0))


def test_every_new_table_joins_the_audit_chain_and_the_chain_holds():
    reaction = PcrReactionFactory()
    CyclingStepFactory()
    MasterMixComponentFactory()

    assert verify_audit_chain().is_intact
    chained = set(AuditChainLink.objects.values_list("event_table", flat=True))
    assert chained >= {
        "pcr_primerevent",
        "pcr_cyclingprogramevent",
        "pcr_cyclingstepevent",
        "pcr_mastermixevent",
        "pcr_mastermixcomponentevent",
        "pcr_assayevent",
        "pcr_pcrrunevent",
        "pcr_pcrreactionevent",
    }
    assert PcrRun.objects.get(pk=reaction.run_id).events.count() == 1
