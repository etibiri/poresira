# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError

from poresira.core.models import GlobalRole, ProjectRole
from poresira.core.tests.factories import (
    ProjectFactory,
    ProjectMembershipFactory,
    UserFactory,
)
from poresira.pcr import services
from poresira.pcr.models import Container, Result, Role, RunStatus
from poresira.pcr.services import Validity
from poresira.pcr.tests.factories import (
    AssayFactory,
    MasterMixComponentFactory,
    MasterMixFactory,
    PcrReactionFactory,
    PcrRunFactory,
    PrimerFactory,
)
from poresira.samples import services as sample_services
from poresira.samples.models import SampleKind, SampleStatus
from poresira.samples.tests.factories import DerivativeFactory, SampleFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return ProjectFactory()


def member(project, project_role=ProjectRole.CONTRIBUTOR, global_role=GlobalRole.TECHNICIAN):
    return ProjectMembershipFactory(
        project=project, role=project_role, user=UserFactory(role=global_role)
    ).user


@pytest.fixture
def editor(project):
    return member(project)


@pytest.fixture
def staff():
    return UserFactory(role=GlobalRole.PLATFORM_MANAGER)


@pytest.fixture
def run(project, editor):
    return services.plan_run(user=editor, project=project, assay=AssayFactory())


def sample_in(project, **kwargs):
    return SampleFactory(project=project, **kwargs)


def performed_run(project, editor, *, wells=3):
    run = services.plan_run(user=editor, project=project, assay=AssayFactory())
    for _number in range(wells):
        services.add_reaction(user=editor, run=run, sample=sample_in(project))
    return services.mark_performed(user=editor, run=run, performed_on=date(2026, 3, 1))


class TestPermissions:
    def test_only_platform_staff_define_reference_data(self, editor, staff):
        for user in (editor, member(ProjectFactory(), ProjectRole.LEAD, GlobalRole.PROJECT_LEAD)):
            with pytest.raises(PermissionDenied):
                services.create_primer(user=user, name="CP-F", sequence="ATGC", direction="forward")
        assert (
            services.create_primer(
                user=staff, name="CP-F", sequence="atgc", direction="forward"
            ).sequence
            == "ATGC"
        )

    def test_viewers_and_outsiders_cannot_plan_or_change_runs(self, project, run):
        viewer = member(project, ProjectRole.VIEWER)
        outsider = UserFactory(role=GlobalRole.TECHNICIAN)
        for user in (viewer, outsider):
            with pytest.raises(PermissionDenied):
                services.plan_run(user=user, project=project, assay=run.assay)
            with pytest.raises(PermissionDenied):
                services.add_reaction(user=user, run=run, sample=sample_in(project))
            with pytest.raises(PermissionDenied):
                services.void_record(user=user, record=run, reason="x")


class TestReferenceData:
    def test_a_program_keeps_its_steps_in_order(self, staff):
        program = services.create_cycling_program(
            user=staff,
            name="SPFMV CP",
            cycles=30,
            steps=[
                {"stage": "initial", "temperature_c": 94, "duration_seconds": 180},
                {"stage": "cycle", "temperature_c": 94, "duration_seconds": 30},
                {"stage": "cycle", "temperature_c": "56.3", "duration_seconds": 30},
                {"stage": "cycle", "temperature_c": 72, "duration_seconds": 60},
                {"stage": "final", "temperature_c": 72, "duration_seconds": 600},
            ],
        )

        assert [step.order for step in program.steps.all()] == [1, 2, 3, 4, 5]
        assert program.steps.get(order=3).temperature_c == Decimal("56.3")

    @pytest.mark.parametrize(
        "stages",
        [["cycle", "initial"], ["initial", "final"], ["final", "cycle"]],
    )
    def test_steps_must_follow_initial_cycle_final_and_include_a_cycle(self, staff, stages):
        steps = [{"stage": stage, "temperature_c": 60, "duration_seconds": 30} for stage in stages]

        with pytest.raises(ValidationError):
            services.create_cycling_program(user=staff, name="bad", cycles=30, steps=steps)

    def test_a_failed_program_leaves_nothing_behind(self, staff):
        from poresira.pcr.models import CyclingProgram

        with pytest.raises(ValidationError):
            services.create_cycling_program(
                user=staff,
                name="half",
                cycles=30,
                steps=[
                    {"stage": "cycle", "temperature_c": 94, "duration_seconds": 30},
                    {"stage": "cycle", "temperature_c": 999, "duration_seconds": 30},
                ],
            )

        assert not CyclingProgram.objects.filter(name="half").exists()

    def test_ingredients_cannot_exceed_the_reaction_volume(self, staff):
        with pytest.raises(ValidationError):
            services.create_master_mix(
                user=staff,
                name="too much",
                reaction_volume_ul=50,
                components=[{"name": "water", "volume_ul": 51}],
            )

    def test_an_assay_needs_a_forward_and_a_reverse_primer(self, staff):
        forward, reverse = PrimerFactory(), PrimerFactory(direction="reverse")
        assay = AssayFactory.build()

        with pytest.raises(ValidationError):
            services.create_assay(
                user=staff,
                name="swapped",
                target="x",
                forward_primer=reverse,
                reverse_primer=forward,
                cycling_program=assay.cycling_program,
            )

    def test_voided_ingredients_cannot_build_a_new_assay(self, staff):
        forward, reverse = PrimerFactory(), PrimerFactory(direction="reverse")
        forward.void(user=staff, reason="Wrong sequence")

        with pytest.raises(ValidationError):
            services.create_assay(
                user=staff,
                name="a",
                target="x",
                forward_primer=forward,
                reverse_primer=reverse,
                cycling_program=AssayFactory().cycling_program,
            )

    def test_something_in_use_cannot_be_voided(self, staff):
        assay = AssayFactory()

        with pytest.raises(services.RecordInUseError):
            services.void_record(user=staff, record=assay.forward_primer, reason="x")
        with pytest.raises(services.RecordInUseError):
            services.void_record(user=staff, record=assay.cycling_program, reason="x")
        services.void_record(user=staff, record=assay, reason="Replaced")
        services.void_record(user=staff, record=assay.forward_primer, reason="Replaced")


class TestLayout:
    @pytest.mark.parametrize(
        ("given", "expected"), [("a1", "A1"), (" b03 ", "B3"), ("H12", "H12"), ("c010", "C10")]
    )
    def test_wells_are_normalised(self, run, given, expected):
        assert services.normalize_position(run, given) == expected

    @pytest.mark.parametrize("given", ["I1", "A0", "A13", "1", "AA1", "", "A-1"])
    def test_a_position_outside_the_plate_is_refused(self, run, given):
        with pytest.raises(services.InvalidPositionError):
            services.normalize_position(run, given)

    def test_tubes_are_numbered_up_to_the_count(self, project, editor):
        run = services.plan_run(
            user=editor,
            project=project,
            assay=AssayFactory(),
            container=Container.TUBES,
            tube_count=8,
        )

        assert services.normalize_position(run, "03") == "3"
        assert services.all_positions(run) == [str(n) for n in range(1, 9)]
        for bad in ("0", "9", "A1"):
            with pytest.raises(services.InvalidPositionError):
                services.normalize_position(run, bad)

    def test_a_run_in_tubes_needs_a_count(self, project, editor):
        with pytest.raises(ValidationError):
            services.plan_run(
                user=editor, project=project, assay=AssayFactory(), container=Container.TUBES
            )

    def test_a_plate_fills_column_by_column(self, project, editor, run):
        for _n in range(10):
            services.add_reaction(user=editor, run=run, sample=sample_in(project))

        positions = list(run.reactions.order_by("created_at").values_list("position", flat=True))

        assert positions == ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1", "A2", "B2"]

    def test_a_full_run_has_no_next_position(self, project, editor):
        run = services.plan_run(
            user=editor,
            project=project,
            assay=AssayFactory(),
            container=Container.TUBES,
            tube_count=2,
        )
        for _n in range(2):
            services.add_reaction(user=editor, run=run, sample=sample_in(project))

        with pytest.raises(services.PositionTakenError):
            services.add_reaction(user=editor, run=run, sample=sample_in(project))

    def test_a_taken_position_is_refused_clearly(self, project, editor, run):
        services.add_reaction(user=editor, run=run, position="a1", sample=sample_in(project))

        with pytest.raises(services.PositionTakenError):
            services.add_reaction(user=editor, run=run, position="A1", sample=sample_in(project))

    def test_the_layout_is_fixed_once_performed(self, project, editor):
        run = performed_run(project, editor)

        with pytest.raises(services.RunStateError):
            services.add_reaction(user=editor, run=run, sample=sample_in(project))


class TestTemplates:
    def test_a_template_must_be_usable(self, project, editor, run):
        field = sample_in(project)
        plant = DerivativeFactory(parent=field, kind=SampleKind.LIVING_PLANT)
        extract = DerivativeFactory(parent=plant, kind=SampleKind.DNA_EXTRACT)
        used_up = DerivativeFactory(parent=plant, kind=SampleKind.RNA_EXTRACT)
        sample_services.change_sample(
            user=editor, sample=used_up, status=SampleStatus.CONSUMED, reason="all used"
        )
        voided = sample_in(project)
        voided.void(user=editor, reason="mistake")
        refused = [plant, used_up, voided, SampleFactory()]  # the last one: another project

        for sample in refused:
            with pytest.raises(ValidationError):
                services.add_reaction(user=editor, run=run, sample=sample)
        assert services.add_reaction(user=editor, run=run, sample=extract).sample == extract

    def test_the_template_volume_must_fit_in_the_master_mix(self, project, editor):
        mix = MasterMixFactory(reaction_volume_ul=50)
        MasterMixComponentFactory(master_mix=mix, name="water", volume_ul=45)
        run = services.plan_run(user=editor, project=project, assay=AssayFactory(master_mix=mix))

        with pytest.raises(ValidationError):
            services.add_reaction(
                user=editor, run=run, sample=sample_in(project), template_volume_ul=6
            )
        reaction = services.add_reaction(
            user=editor, run=run, sample=sample_in(project), template_volume_ul=5
        )
        assert reaction.template_volume_ul == 5

    def test_controls_need_no_template(self, editor, run):
        control = services.add_reaction(user=editor, run=run, role=Role.NEGATIVE_CONTROL)

        assert control.sample is None

    def test_a_sample_reaction_without_a_template_is_refused(self, editor, run):
        with pytest.raises(ValidationError):
            services.add_reaction(user=editor, run=run, role=Role.SAMPLE)

    def test_a_sample_with_pcr_results_cannot_be_voided_first(self, project, editor, run):
        sample = sample_in(project)
        reaction = services.add_reaction(user=editor, run=run, sample=sample)

        with pytest.raises(sample_services.RecordInUseError):
            sample_services.void_record(user=editor, record=sample, reason="x")
        services.void_record(user=editor, record=reaction, reason="wrong tube")
        sample_services.void_record(user=editor, record=sample, reason="mistake")


class TestRunLife:
    def test_a_run_goes_from_planned_to_performed_to_closed(self, project, editor):
        run = performed_run(project, editor)
        assert (run.status, run.performed_by) == (RunStatus.PERFORMED, editor)

        for reaction in run.reactions.all():
            services.record_result(
                user=editor, reaction=reaction, result=Result.NEGATIVE, notes="no band"
            )
        services.close_run(user=editor, run=run)

        assert run.status == RunStatus.CLOSED

    def test_a_run_needs_reactions_and_a_date_before_it_is_performed(self, project, editor, run):
        with pytest.raises(services.RunStateError):
            services.mark_performed(user=editor, run=run, performed_on=date(2026, 3, 1))
        services.add_reaction(user=editor, run=run, sample=sample_in(project))
        with pytest.raises(ValidationError):
            services.mark_performed(user=editor, run=run)
        with pytest.raises(ValidationError):
            services.mark_performed(
                user=editor, run=run, performed_on=date.today() + timedelta(days=1)
            )

    def test_results_wait_for_the_run_to_be_performed(self, project, editor, run):
        reaction = services.add_reaction(user=editor, run=run, sample=sample_in(project))

        with pytest.raises(services.RunStateError):
            services.record_result(user=editor, reaction=reaction, result=Result.POSITIVE)

    def test_a_result_cannot_go_back_to_pending(self, project, editor):
        run = performed_run(project, editor)
        reaction = run.reactions.first()
        services.record_result(user=editor, reaction=reaction, result=Result.POSITIVE)

        with pytest.raises(ValidationError):
            services.record_result(user=editor, reaction=reaction, result=Result.PENDING)

    def test_a_run_cannot_close_with_pending_results(self, project, editor):
        run = performed_run(project, editor)

        with pytest.raises(services.RunStateError):
            services.close_run(user=editor, run=run)

    def test_correcting_a_closed_run_needs_a_reason_that_reaches_the_audit_log(
        self, project, editor
    ):
        run = performed_run(project, editor, wells=1)
        reaction = run.reactions.get()
        services.record_result(user=editor, reaction=reaction, result=Result.NEGATIVE)
        services.close_run(user=editor, run=run)

        with pytest.raises(ValueError):
            services.record_result(
                user=editor, reaction=reaction, result=Result.POSITIVE, reason=" "
            )
        services.record_result(
            user=editor,
            reaction=reaction,
            result=Result.POSITIVE,
            band_size_bp=700,
            reason="Faint band seen on the second gel",
        )

        event = reaction.events.filter(pgh_label="update").latest("pgh_id")
        assert event.result == Result.POSITIVE
        assert event.pgh_context.metadata["reason"] == "Faint band seen on the second gel"

    def test_reopening_needs_a_reason_and_allows_changes_again(self, project, editor):
        run = performed_run(project, editor, wells=1)
        services.record_result(user=editor, reaction=run.reactions.get(), result=Result.NEGATIVE)
        services.close_run(user=editor, run=run)

        with pytest.raises(ValueError):
            services.reopen_run(user=editor, run=run, reason="")
        services.reopen_run(user=editor, run=run, reason="Gel to be repeated")

        assert run.status == RunStatus.PERFORMED
        services.record_result(user=editor, reaction=run.reactions.get(), result=Result.POSITIVE)

    def test_the_database_still_refuses_an_unexplained_change_on_a_closed_run(
        self, project, editor
    ):
        run = performed_run(project, editor, wells=1)
        services.record_result(user=editor, reaction=run.reactions.get(), result=Result.NEGATIVE)
        services.close_run(user=editor, run=run)
        reaction = run.reactions.get()
        reaction.result = Result.POSITIVE

        with pytest.raises(DatabaseError):
            reaction.save()

    def test_a_run_with_reactions_cannot_be_voided(self, project, editor):
        run = performed_run(project, editor, wells=1)

        with pytest.raises(services.RecordInUseError):
            services.void_record(user=editor, record=run, reason="x")
        services.void_record(user=editor, record=run.reactions.get(), reason="wrong plate")
        services.void_record(user=editor, record=run, reason="wrong plate")

    def test_a_reconstructed_run_needs_no_date(self, project, editor):
        run = services.plan_run(
            user=editor, project=project, assay=AssayFactory(), reconstructed=True
        )
        services.add_reaction(user=editor, run=run, sample=sample_in(project))

        services.mark_performed(user=editor, run=run)

        assert run.performed_on is None


class TestValidity:
    def run_with(self, project, editor, **results):
        run = services.plan_run(user=editor, project=project, assay=AssayFactory())
        roles = {
            "positive": Role.POSITIVE_CONTROL,
            "negative": Role.NEGATIVE_CONTROL,
            "blank": Role.EXTRACTION_BLANK,
        }
        for name in results:
            sample = sample_in(project) if name == "sample" else None
            services.add_reaction(
                user=editor, run=run, role=roles.get(name, Role.SAMPLE), sample=sample
            )
        services.mark_performed(user=editor, run=run, performed_on=date(2026, 3, 1))
        for name, result in results.items():
            role = roles.get(name, Role.SAMPLE)
            services.record_result(
                user=editor, reaction=run.reactions.get(role=role), result=result
            )
        return run

    def test_controls_that_behave_make_the_run_valid(self, project, editor):
        run = self.run_with(
            project, editor, positive="positive", negative="negative", sample="positive"
        )

        assert services.assess_run(run).state == Validity.VALID

    def test_a_positive_control_that_fails_invalidates_the_run(self, project, editor):
        run = self.run_with(project, editor, positive="negative", negative="negative")

        verdict = services.assess_run(run)

        assert verdict.state == Validity.INVALID
        assert "did not amplify" in verdict.problems[0]

    @pytest.mark.parametrize("control", ["negative", "blank"])
    def test_a_contaminated_blank_invalidates_the_run(self, project, editor, control):
        results = {"positive": "positive", "negative": "negative", control: "positive"}
        run = self.run_with(project, editor, **results)

        assert services.assess_run(run).state == Validity.INVALID

    def test_without_both_controls_nothing_is_concluded(self, project, editor):
        run = self.run_with(project, editor, sample="positive", positive="positive")

        verdict = services.assess_run(run)

        assert verdict.state == Validity.NOT_ASSESSED
        assert any("no-template" in problem for problem in verdict.problems)

    def test_a_control_without_a_result_is_not_assessed(self, project, editor):
        run = services.plan_run(user=editor, project=project, assay=AssayFactory())
        services.add_reaction(user=editor, run=run, role=Role.POSITIVE_CONTROL)
        services.add_reaction(user=editor, run=run, role=Role.NEGATIVE_CONTROL)

        assert services.assess_run(run).state == Validity.NOT_ASSESSED

    def test_a_reconstructed_run_is_never_assessed(self, project, editor):
        run = PcrRunFactory(project=project, reconstructed=True)

        assert services.assess_run(run).state == Validity.NOT_ASSESSED


class TestGelBand:
    @pytest.mark.parametrize(
        ("band", "expected"), [(700, True), (640, True), (400, False), (None, None)]
    )
    def test_a_band_is_compared_with_the_expected_size(self, band, expected):
        reaction = PcrReactionFactory(
            run=PcrRunFactory(assay=AssayFactory(expected_amplicon_bp=700)), band_size_bp=band
        )

        assert services.band_matches_expectation(reaction) is expected

    def test_without_an_expected_size_nothing_is_concluded(self):
        reaction = PcrReactionFactory(band_size_bp=700)

        assert services.band_matches_expectation(reaction) is None
