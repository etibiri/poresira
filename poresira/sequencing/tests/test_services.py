# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from poresira.core.models import GlobalRole, ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.samples import services as sample_services
from poresira.samples.models import SampleKind, SampleStatus
from poresira.samples.tests.factories import DerivativeFactory, SampleFactory
from poresira.sequencing import adapters, services
from poresira.sequencing.models import Chemistry, LibraryStatus, RunStatus
from poresira.sequencing.tests.factories import (
    FlowCellFactory,
    FlowCellTypeFactory,
    InstrumentFactory,
    LibraryKitFactory,
    LibraryMemberFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return ProjectFactory()


def member_of(project, project_role=ProjectRole.CONTRIBUTOR, global_role=GlobalRole.TECHNICIAN):
    return ProjectMembershipFactory(
        project=project, role=project_role, user=UserFactory(role=global_role)
    ).user


@pytest.fixture
def editor(project):
    return member_of(project)


@pytest.fixture
def staff():
    return UserFactory(role=GlobalRole.PLATFORM_MANAGER)


@pytest.fixture
def library(project, editor):
    return services.create_library(user=editor, project=project, kit=LibraryKitFactory())


def product(project, **attributes):
    return DerivativeFactory(
        kind=SampleKind.AMPLIFICATION_PRODUCT, parent=SampleFactory(project=project), **attributes
    )


@pytest.fixture
def sealed(library, editor, project):
    services.add_member(user=editor, library=library, sample=product(project), barcode=1)
    services.add_member(user=editor, library=library, sample=product(project), barcode="NB02")
    return services.seal_library(user=editor, library=library)


class TestAdapter:
    @pytest.mark.parametrize(
        ("text", "number"), [("7", 7), ("07", 7), ("NB07", 7), ("barcode07", 7), (" bc 96", 96)]
    )
    def test_barcodes_are_read_in_every_usual_spelling(self, text, number):
        assert adapters.adapter_for("nanopore").parse_barcode(text) == number

    @pytest.mark.parametrize("text", ["", "0", "NB", "seven", "NB-1", "1.5"])
    def test_other_barcodes_are_refused(self, text):
        with pytest.raises(ValidationError):
            adapters.adapter_for("nanopore").parse_barcode(text)

    def test_flow_cell_ids_are_upper_cased_and_checked(self):
        adapter = adapters.adapter_for("nanopore")

        assert adapter.normalize_flow_cell_id(" paw12345 ") == "PAW12345"
        with pytest.raises(ValidationError):
            adapter.normalize_flow_cell_id("PAW 1")

    def test_run_ids_are_normalised(self):
        assert adapters.adapter_for("nanopore").normalize_run_id(" ABC123 ") == "abc123"


class TestReferenceData:
    def test_staff_create_kits_types_and_instruments(self, staff):
        kit = services.create_library_kit(
            user=staff,
            code="sqk-nbd114.96",
            name="Native Barcoding 96 V14",
            chemistry=Chemistry.NATIVE_BARCODING,
            barcode_count=96,
        )
        cell_type = services.create_flow_cell_type(
            user=staff, code="FLO-MIN114", name="MinION R10.4.1", pore="R10.4.1"
        )
        instrument = services.create_instrument(user=staff, name="Mk1C-1", model="MinION Mk1C")

        assert kit.code == "SQK-NBD114.96"
        assert (cell_type.pore, instrument.model) == ("R10.4.1", "MinION Mk1C")

    def test_other_users_cannot(self, editor):
        with pytest.raises(PermissionDenied):
            services.create_instrument(user=editor, name="X", model="Y")
        with pytest.raises(PermissionDenied):
            services.create_library_kit(
                user=editor, code="A", name="A", chemistry=Chemistry.LIGATION
            )

    def test_a_barcoding_kit_needs_its_barcode_count_and_a_plain_kit_has_none(self, staff):
        with pytest.raises(ValidationError):
            services.create_library_kit(
                user=staff, code="A", name="A", chemistry=Chemistry.NATIVE_BARCODING
            )
        with pytest.raises(ValidationError):
            services.create_library_kit(
                user=staff, code="B", name="B", chemistry=Chemistry.LIGATION, barcode_count=12
            )

    def test_a_flow_cell_is_registered_by_a_project_editor(self, editor):
        cell = services.register_flow_cell(
            user=editor, flow_cell_id="paw12345", type=FlowCellTypeFactory(), pores_at_qc=1200
        )

        assert (cell.flow_cell_id, cell.pores_at_qc) == ("PAW12345", 1200)
        with pytest.raises(PermissionDenied):
            services.register_flow_cell(
                user=UserFactory(role=GlobalRole.GUEST),
                flow_cell_id="PAW9",
                type=FlowCellTypeFactory(),
            )


class TestLibrary:
    def test_a_library_is_created_by_a_project_editor_and_starts_as_a_draft(self, project, editor):
        library = services.create_library(
            user=editor, project=project, kit=LibraryKitFactory(), name="DNA virome"
        )

        assert library.status == LibraryStatus.DRAFT
        assert library.prepared_by == editor

    def test_viewers_and_outsiders_cannot_create_one(self, project):
        for user in (member_of(project, ProjectRole.VIEWER), UserFactory()):
            with pytest.raises(PermissionDenied):
                services.create_library(user=user, project=project, kit=LibraryKitFactory())

    def test_a_voided_kit_cannot_be_used(self, project, editor, staff):
        kit = LibraryKitFactory()
        services.void_record(user=staff, record=kit, reason="Discontinued")

        with pytest.raises(ValidationError):
            services.create_library(user=editor, project=project, kit=kit)

    def test_the_preparation_date_cannot_be_in_the_future(self, project, editor):
        with pytest.raises(ValidationError):
            services.create_library(
                user=editor,
                project=project,
                kit=LibraryKitFactory(),
                prepared_on=date.today() + timedelta(days=1),
            )

    def test_unknown_attributes_are_refused(self, project, editor):
        with pytest.raises(TypeError):
            services.create_library(
                user=editor, project=project, kit=LibraryKitFactory(), status="sealed"
            )


class TestMembers:
    def test_samples_join_under_their_barcode(self, library, editor, project):
        member = services.add_member(
            user=editor, library=library, sample=product(project), barcode="barcode07", input_ng=50
        )

        assert member.barcode == 7

    @pytest.mark.parametrize(
        ("barcode", "message"),
        [(97, "outside the kit"), (None, "required"), ("x", "not a barcode")],
    )
    def test_bad_barcodes_are_explained(self, library, editor, project, barcode, message):
        with pytest.raises(ValidationError, match=message):
            services.add_member(
                user=editor, library=library, sample=product(project), barcode=barcode
            )

    def test_a_barcode_and_a_sample_are_used_once(self, library, editor, project):
        first = services.add_member(
            user=editor, library=library, sample=product(project), barcode=1
        )

        with pytest.raises(ValidationError, match="already used"):
            services.add_member(user=editor, library=library, sample=product(project), barcode=1)
        with pytest.raises(ValidationError, match="already in the library"):
            services.add_member(user=editor, library=library, sample=first.sample, barcode=2)

    def test_only_usable_material_of_the_project_is_accepted(self, library, editor, project):
        used_up = product(project)
        sample_services.change_sample(
            user=editor, sample=used_up, status=SampleStatus.CONSUMED, reason="all used"
        )
        for sample in (
            SampleFactory(project=project),  # a field sample
            product(ProjectFactory()),  # another project
            used_up,
        ):
            with pytest.raises(ValidationError):
                services.add_member(user=editor, library=library, sample=sample, barcode=3)

    def test_a_reconstructed_library_may_hold_samples_without_barcodes(self, project, editor):
        library = services.create_library(
            user=editor, project=project, kit=LibraryKitFactory(), reconstructed=True
        )

        member = services.add_member(user=editor, library=library, sample=product(project))
        assert member.barcode is None

    def test_a_sealed_library_takes_no_more_samples(self, sealed, editor, project):
        with pytest.raises(services.LibraryStateError):
            services.add_member(user=editor, library=sealed, sample=product(project), barcode=9)

    def test_a_library_is_sealed_only_with_members(self, library, editor):
        with pytest.raises(services.LibraryStateError, match="at least one"):
            services.seal_library(user=editor, library=library)


class TestRuns:
    def test_a_run_needs_a_sealed_library_and_a_free_flow_cell(self, project, sealed, editor):
        draft = services.create_library(user=editor, project=project, kit=LibraryKitFactory())
        with pytest.raises(services.LibraryStateError):
            services.plan_run(user=editor, library=draft, flow_cell=FlowCellFactory())
        run = services.plan_run(user=editor, library=sealed, flow_cell=FlowCellFactory())

        assert run.status == RunStatus.PLANNED
        with pytest.raises(ValidationError, match="run in progress"):
            services.plan_run(user=editor, library=sealed, flow_cell=run.flow_cell)

    def test_a_flow_cell_can_serve_again_once_its_run_is_voided(self, sealed, editor):
        run = services.plan_run(user=editor, library=sealed, flow_cell=FlowCellFactory())
        services.void_record(user=editor, record=run, reason="Never loaded")

        assert services.plan_run(user=editor, library=sealed, flow_cell=run.flow_cell)

    def test_a_reusable_flow_cell_is_washed_and_loaded_again(self, sealed, editor, project):
        cell = FlowCellFactory()
        first = services.plan_run(user=editor, library=sealed, flow_cell=cell, pores_at_start=9000)
        services.start_run(user=editor, run=first, started_on=date(2025, 3, 1))
        services.complete_run(
            user=editor, run=first, basecaller="Dorado", basecaller_version="0.9.0"
        )
        other = services.create_library(user=editor, project=project, kit=LibraryKitFactory())
        services.add_member(user=editor, library=other, sample=product(project), barcode=1)
        services.seal_library(user=editor, library=other)

        second = services.plan_run(user=editor, library=other, flow_cell=cell, pores_at_start=4000)

        assert [r.pores_at_start for r in cell.runs.order_by("pores_at_start")] == [4000, 9000]
        with pytest.raises(services.RunStateError, match="another run in progress"):
            services.reopen_run(user=editor, run=first, reason="Wrong version")
        assert second.status == RunStatus.PLANNED

    def test_a_single_use_flow_cell_serves_once(self, sealed, editor, project):
        cell = FlowCellFactory(type=FlowCellTypeFactory(reusable=False))
        run = services.plan_run(user=editor, library=sealed, flow_cell=cell)
        services.start_run(user=editor, run=run, started_on=date(2025, 3, 1))
        services.complete_run(user=editor, run=run, basecaller="Dorado", basecaller_version="0.9.0")
        other = services.create_library(user=editor, project=project, kit=LibraryKitFactory())
        services.add_member(user=editor, library=other, sample=product(project), barcode=1)
        services.seal_library(user=editor, library=other)

        with pytest.raises(ValidationError, match="single-use"):
            services.plan_run(user=editor, library=other, flow_cell=cell)

    def test_starting_needs_a_date_unless_reconstructed(self, sealed, editor):
        run = services.plan_run(user=editor, library=sealed, flow_cell=FlowCellFactory())

        with pytest.raises(ValidationError):
            services.start_run(user=editor, run=run)
        assert run.status == RunStatus.PLANNED
        with pytest.raises(ValidationError):
            services.start_run(user=editor, run=run, started_on=date.today() + timedelta(days=2))
        services.start_run(
            user=editor,
            run=run,
            started_on=date(2025, 3, 1),
            run_id=" ABC123 ",
            instrument=InstrumentFactory(),
        )
        assert (run.status, run.run_id, run.performed_by) == (RunStatus.RUNNING, "abc123", editor)

    def test_the_whole_life_of_a_run(self, sealed, editor):
        run = services.plan_run(user=editor, library=sealed, flow_cell=FlowCellFactory())
        services.start_run(user=editor, run=run, started_on=date(2025, 3, 1))
        first, second = sealed.members.order_by("barcode")
        services.record_result(user=editor, run=run, member=first, reads=900, n50=1800)
        services.record_result(user=editor, run=run, member=second, reads=100)

        services.complete_run(
            user=editor,
            run=run,
            basecaller="Dorado",
            basecaller_version="0.9.0",
            basecall_model="dna_r10.4.1_e8.2_400bps_sup@v5.0.0",
            total_reads=1100,
        )
        assert run.status == RunStatus.COMPLETED

    def test_completion_needs_the_basecaller_unless_reconstructed(self, sealed, editor):
        run = services.plan_run(user=editor, library=sealed, flow_cell=FlowCellFactory())
        services.start_run(user=editor, run=run, started_on=date(2025, 3, 1))

        with pytest.raises(ValidationError):
            services.complete_run(user=editor, run=run)
        assert run.status == RunStatus.RUNNING

    def test_the_reads_of_the_samples_cannot_exceed_those_of_the_run(self, sealed, editor):
        run = services.plan_run(user=editor, library=sealed, flow_cell=FlowCellFactory())
        services.start_run(user=editor, run=run, started_on=date(2025, 3, 1))
        services.record_result(user=editor, run=run, member=sealed.members.first(), reads=500)

        with pytest.raises(ValidationError, match="exceed"):
            services.complete_run(
                user=editor,
                run=run,
                basecaller="Dorado",
                basecaller_version="0.9.0",
                total_reads=400,
            )

    def test_an_aborted_run_needs_a_reason_and_stops_there(self, sealed, editor):
        run = services.plan_run(user=editor, library=sealed, flow_cell=FlowCellFactory())

        with pytest.raises(ValidationError):
            services.abort_run(user=editor, run=run, reason=" ")
        services.abort_run(user=editor, run=run, reason="Flow cell had no pores")
        assert run.status == RunStatus.ABORTED
        with pytest.raises(services.RunStateError):
            services.start_run(user=editor, run=run, started_on=date(2025, 3, 1))

    def test_other_projects_cannot_touch_a_run(self, sealed, editor):
        run = services.plan_run(user=editor, library=sealed, flow_cell=FlowCellFactory())

        with pytest.raises(PermissionDenied):
            services.start_run(user=UserFactory(), run=run, started_on=date(2025, 3, 1))


class TestResults:
    @pytest.fixture
    def running(self, sealed, editor):
        run = services.plan_run(user=editor, library=sealed, flow_cell=FlowCellFactory())
        return services.start_run(user=editor, run=run, started_on=date(2025, 3, 1))

    def test_a_result_is_entered_then_corrected(self, running, editor):
        member = running.library.members.first()
        first = services.record_result(user=editor, run=running, member=member, reads=10)
        second = services.record_result(
            user=editor, run=running, member=member, reads=20, mean_quality="14.2"
        )

        assert first.pk == second.pk and second.reads == 20
        assert running.results.count() == 1

    def test_a_result_must_concern_a_member_of_the_library(self, running, editor):
        stranger = LibraryMemberFactory()

        with pytest.raises(ValidationError):
            services.record_result(user=editor, run=running, member=stranger, reads=1)

    def test_nothing_can_be_entered_before_the_run_starts(self, sealed, editor):
        run = services.plan_run(user=editor, library=sealed, flow_cell=FlowCellFactory())

        with pytest.raises(services.RunStateError):
            services.record_result(user=editor, run=run, member=sealed.members.first(), reads=1)

    def test_after_completion_a_correction_needs_a_reason(self, running, editor):
        member = running.library.members.first()
        services.record_result(user=editor, run=running, member=member, reads=10)
        services.complete_run(
            user=editor, run=running, basecaller="Dorado", basecaller_version="0.9.0"
        )

        with pytest.raises(Exception, match="reason"):
            services.record_result(user=editor, run=running, member=member, reads=11)
        assert running.results.get().reads == 10
        services.record_result(
            user=editor, run=running, member=member, reads=11, reason="Recounted"
        )
        assert running.results.get().reads == 11

    def test_a_completed_run_reopens_only_with_a_reason(self, running, editor):
        services.complete_run(
            user=editor, run=running, basecaller="Dorado", basecaller_version="0.9.0"
        )

        with pytest.raises(Exception, match="reason"):
            services.reopen_run(user=editor, run=running, reason=" ")
        services.reopen_run(user=editor, run=running, reason="Wrong basecaller version")
        assert running.status == RunStatus.RUNNING


class TestVoiding:
    def test_a_library_is_voided_from_the_inside_out(self, sealed, editor):
        with pytest.raises(services.RecordInUseError):
            services.void_record(user=editor, record=sealed, reason="x")
        for member in sealed.members.all():
            services.void_record(user=editor, record=member, reason="Wrong tube")
        services.void_record(user=editor, record=sealed, reason="Cancelled")

        assert sealed.is_voided

    def test_a_member_in_use_by_a_run_stays(self, sealed, editor):
        services.plan_run(user=editor, library=sealed, flow_cell=FlowCellFactory())

        with pytest.raises(services.RecordInUseError):
            services.void_record(user=editor, record=sealed.members.first(), reason="x")

    def test_runs_flow_cells_and_reference_data_are_protected_while_used(
        self, sealed, editor, staff
    ):
        run = services.plan_run(
            user=editor, library=sealed, flow_cell=FlowCellFactory(), instrument=InstrumentFactory()
        )
        for record in (run.flow_cell, run.instrument, sealed.kit, run.flow_cell.type):
            with pytest.raises(services.RecordInUseError):
                services.void_record(user=staff, record=record, reason="x")
        with pytest.raises(services.RecordInUseError):
            services.void_record(user=editor, record=sealed, reason="x")

    def test_a_sample_in_a_library_cannot_be_voided(self, sealed, editor):
        sample = sealed.members.first().sample

        with pytest.raises(sample_services.RecordInUseError):
            sample_services.void_record(user=editor, record=sample, reason="x")

    def test_reference_data_is_voided_by_staff_only(self, editor, staff):
        instrument = InstrumentFactory()

        with pytest.raises(PermissionDenied):
            services.void_record(user=editor, record=instrument, reason="x")
        services.void_record(user=staff, record=instrument, reason="Retired")
        assert instrument.is_voided
