# SPDX-License-Identifier: AGPL-3.0-or-later
import threading
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connections

from poresira.core.models import GlobalRole, ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.samples import services
from poresira.samples.models import (
    QcMethod,
    QcOutcome,
    Sample,
    SampleKind,
    SampleStatus,
    StorageLocationKind,
)
from poresira.samples.services import (
    InvalidDerivationError,
    InvalidPositionError,
    PositionOccupiedError,
    RecordInUseError,
    SampleServiceError,
)
from poresira.samples.tests.factories import (
    CollectionEventFactory,
    DerivativeFactory,
    SampleFactory,
    StorageLocationFactory,
)

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
def event(project, editor):
    return services.create_collection_event(
        user=editor, project=project, locality="Tiébélé", province="Nahouri"
    )


@pytest.fixture
def field_sample(editor, event):
    return services.register_field_sample(
        user=editor,
        collection_event=event,
        label="BFA266",
        host_taxon="Ipomoea batatas",
        host_tax_id=4120,
        symptom_score=5,
    )


@pytest.fixture
def box():
    manager = UserFactory(role=GlobalRole.PLATFORM_MANAGER)
    return services.create_storage_location(
        user=manager, name="Box 1", kind=StorageLocationKind.BOX, grid_rows=9, grid_columns=9
    )


class TestPermissions:
    @pytest.mark.parametrize(
        "make_user",
        [
            lambda project: member(project, ProjectRole.VIEWER),
            lambda project: member(project, ProjectRole.LEAD, GlobalRole.GUEST),
            lambda project: UserFactory(),
        ],
        ids=["viewer", "guest", "outsider"],
    )
    def test_users_who_cannot_edit_are_refused_everywhere(self, project, event, make_user):
        user = make_user(project)
        sample = SampleFactory(project=project, collection_event=event)

        calls = [
            lambda: services.create_collection_event(user=user, project=project),
            lambda: services.register_field_sample(user=user, collection_event=event),
            lambda: services.derive_sample(user=user, parent=sample, kind=SampleKind.DNA_EXTRACT),
            lambda: services.edit_sample(user=user, sample=sample, notes="x"),
            lambda: services.change_sample(
                user=user, sample=sample, status=SampleStatus.STORED, reason="r"
            ),
            lambda: services.record_qc(
                user=user, sample=sample, method=QcMethod.QUBIT, concentration_ng_ul=1
            ),
            lambda: services.void_record(user=user, record=sample, reason="r"),
        ]
        for call in calls:
            with pytest.raises(PermissionDenied):
                call()
        assert Sample.objects.count() == 1

    def test_administrators_can_work_in_any_project(self, event):
        admin = UserFactory(role=GlobalRole.ADMINISTRATOR)

        assert services.register_field_sample(user=admin, collection_event=event).pk

    def test_nobody_can_add_to_a_voided_project(self, project, editor):
        project.void(user=UserFactory(role=GlobalRole.ADMINISTRATOR), reason="Closed")

        with pytest.raises(PermissionDenied):
            services.create_collection_event(user=editor, project=project)

    def test_only_platform_staff_manage_storage(self, editor):
        with pytest.raises(PermissionDenied):
            services.create_storage_location(
                user=editor, name="Freezer", kind=StorageLocationKind.FREEZER
            )
        for role in (GlobalRole.PLATFORM_MANAGER, GlobalRole.ADMINISTRATOR):
            services.create_storage_location(
                user=UserFactory(role=role), name=f"Freezer {role}", kind="freezer"
            )


class TestCreation:
    def test_collection_event_gets_an_identifier_and_the_actor_in_the_audit_log(
        self, event, editor
    ):
        created = event.events.get(pgh_label="insert")

        assert event.identifier.split("-")[1].startswith("C")
        assert created.pgh_context.metadata["user"] == str(editor.pk)

    def test_unknown_attributes_are_refused(self, project, editor):
        with pytest.raises(TypeError, match="oops"):
            services.create_collection_event(user=editor, project=project, oops=1)

    def test_invalid_values_raise_validation_errors_and_save_nothing(self, project, editor):
        with pytest.raises(ValidationError):
            services.create_collection_event(
                user=editor, project=project, latitude=Decimal("95"), longitude=Decimal("0")
            )

    def test_field_sample_belongs_to_the_event_project(self, field_sample, event, project):
        assert (field_sample.project, field_sample.collection_event) == (project, event)
        assert field_sample.kind == SampleKind.FIELD_SAMPLE
        assert field_sample.status == SampleStatus.REGISTERED

    def test_duplicate_labels_are_a_validation_error(self, editor, event, field_sample):
        with pytest.raises(ValidationError):
            services.register_field_sample(user=editor, collection_event=event, label="BFA266")

    def test_symptom_score_is_validated(self, editor, event):
        with pytest.raises(ValidationError):
            services.register_field_sample(user=editor, collection_event=event, symptom_score=12)

    def test_events_can_be_edited_but_not_moved_to_another_project(self, editor, event):
        services.edit_collection_event(user=editor, event=event, locality="Kangala")

        assert event.events.get(pgh_label="update").locality == "Kangala"


class TestDerivation:
    def test_a_derivative_inherits_project_event_and_host(self, editor, field_sample):
        extract = services.derive_sample(
            user=editor, parent=field_sample, kind=SampleKind.DNA_EXTRACT, volume_ul=50
        )

        assert extract.parent == field_sample
        assert (extract.project, extract.collection_event) == (
            field_sample.project,
            field_sample.collection_event,
        )
        assert (extract.host_taxon, extract.host_tax_id) == ("Ipomoea batatas", 4120)
        assert extract.symptom_score is None
        assert extract.identifier.split("-")[1].startswith("D")

    @pytest.mark.parametrize(
        ("parent_kind", "child_kind", "allowed"),
        [
            (SampleKind.FIELD_SAMPLE, SampleKind.LIVING_PLANT, True),
            (SampleKind.FIELD_SAMPLE, SampleKind.AMPLIFICATION_PRODUCT, False),
            (SampleKind.DNA_EXTRACT, SampleKind.AMPLIFICATION_PRODUCT, True),
            (SampleKind.DNA_EXTRACT, SampleKind.RNA_EXTRACT, False),
            (SampleKind.RNA_EXTRACT, SampleKind.AMPLIFICATION_PRODUCT, False),
            (SampleKind.LIVING_PLANT, SampleKind.RNA_EXTRACT, True),
            (SampleKind.AMPLIFICATION_PRODUCT, SampleKind.DNA_EXTRACT, False),
        ],
    )
    def test_only_sensible_derivations_are_allowed(
        self, editor, field_sample, parent_kind, child_kind, allowed
    ):
        parent = (
            field_sample
            if parent_kind == SampleKind.FIELD_SAMPLE
            else DerivativeFactory(
                parent=field_sample,
                project=field_sample.project,
                collection_event=field_sample.collection_event,
                kind=parent_kind,
            )
        )

        if allowed:
            assert services.derive_sample(user=editor, parent=parent, kind=child_kind).pk
        else:
            with pytest.raises(InvalidDerivationError):
                services.derive_sample(user=editor, parent=parent, kind=child_kind)

    def test_a_voided_sample_cannot_be_a_parent(self, editor, field_sample):
        field_sample.void(user=editor, reason="Wrong tube")

        with pytest.raises(InvalidDerivationError, match="voided"):
            services.derive_sample(user=editor, parent=field_sample, kind=SampleKind.DNA_EXTRACT)


class TestMoving:
    def test_a_move_normalises_the_position_stores_the_sample_and_records_the_reason(
        self, editor, field_sample, box
    ):
        services.move_sample(
            user=editor, sample=field_sample, location=box, position=" a03 ", reason="Banking"
        )
        field_sample.refresh_from_db()
        move_event = field_sample.events.filter(pgh_label="update").latest("pgh_id")

        assert (field_sample.storage_location, field_sample.position) == (box, "A3")
        assert field_sample.status == SampleStatus.STORED
        assert move_event.pgh_context.metadata["reason"] == "Banking"
        assert move_event.pgh_context.metadata["user"] == str(editor.pk)

    def test_a_reason_is_mandatory_and_nothing_changes_without_one(self, editor, field_sample, box):
        with pytest.raises(ValueError, match="reason"):
            services.move_sample(
                user=editor, sample=field_sample, location=box, position="A1", reason="  "
            )

        field_sample.refresh_from_db()
        assert field_sample.storage_location is None

    @pytest.mark.parametrize("position", ["J1", "A10", "A0", "1A", "AA1", "A-1", "ZZ"])
    def test_positions_must_be_inside_the_grid(self, editor, field_sample, box, position):
        with pytest.raises(InvalidPositionError):
            services.move_sample(
                user=editor, sample=field_sample, location=box, position=position, reason="x"
            )

    @pytest.mark.parametrize("position", ["A1", "a01", "I9", "i009"])
    def test_every_cell_of_the_grid_is_reachable(self, editor, field_sample, box, position):
        services.move_sample(
            user=editor, sample=field_sample, location=box, position=position, reason="x"
        )

    def test_a_position_needs_a_grid(self, editor, field_sample):
        shelf = StorageLocationFactory(name="Shelf 2", kind="shelf")

        with pytest.raises(InvalidPositionError, match="no grid"):
            services.move_sample(
                user=editor, sample=field_sample, location=shelf, position="A1", reason="x"
            )
        services.move_sample(user=editor, sample=field_sample, location=shelf, reason="Shelved")

    def test_a_position_needs_a_location(self, editor, field_sample):
        with pytest.raises(InvalidPositionError, match="needs a storage location"):
            services.move_sample(
                user=editor, sample=field_sample, location=None, position="A1", reason="x"
            )

    def test_an_occupied_position_names_its_occupant(self, editor, event, field_sample, box):
        services.move_sample(
            user=editor, sample=field_sample, location=box, position="A1", reason="Banking"
        )
        second = services.register_field_sample(user=editor, collection_event=event, label="BFA7")

        with pytest.raises(PositionOccupiedError, match=field_sample.identifier):
            services.move_sample(
                user=editor, sample=second, location=box, position="a1", reason="Banking"
            )

    def test_moving_a_sample_onto_its_own_position_changes_nothing(self, editor, field_sample, box):
        services.move_sample(
            user=editor, sample=field_sample, location=box, position="A1", reason="Banking"
        )
        updates_before = field_sample.events.count()

        services.move_sample(
            user=editor, sample=field_sample, location=box, position="A1", reason="Again"
        )

        assert field_sample.events.count() == updates_before

    def test_a_sample_can_be_taken_out_of_storage(self, editor, field_sample, box):
        services.move_sample(
            user=editor, sample=field_sample, location=box, position="A1", reason="Banking"
        )
        services.move_sample(
            user=editor, sample=field_sample, location=None, reason="Taken for extraction"
        )
        field_sample.refresh_from_db()

        assert (field_sample.storage_location, field_sample.position) == (None, "")
        assert field_sample.status == SampleStatus.STORED

    def test_voided_samples_and_locations_cannot_be_used(self, editor, field_sample, box):
        manager = UserFactory(role=GlobalRole.PLATFORM_MANAGER)
        services.void_record(user=manager, record=box, reason="Box discarded")

        with pytest.raises(InvalidPositionError, match="voided"):
            services.move_sample(user=editor, sample=field_sample, location=box, reason="x")
        field_sample.void(user=editor, reason="Wrong tube")
        with pytest.raises(SampleServiceError, match="voided"):
            services.move_sample(user=editor, sample=field_sample, location=None, reason="x")

    @pytest.mark.django_db(transaction=True)
    def test_two_simultaneous_moves_to_one_position_leave_exactly_one_winner(self):
        project = ProjectFactory()
        editor = member(project)
        event = CollectionEventFactory(project=project)
        samples = [SampleFactory(project=project, collection_event=event) for _ in range(2)]
        box = StorageLocationFactory(name="Race box", kind="box", grid_rows=2, grid_columns=2)
        outcomes, start_together = [], threading.Barrier(2)

        def move(sample):
            try:
                start_together.wait()
                services.move_sample(
                    user=editor, sample=sample, location=box, position="A1", reason="Race"
                )
                outcomes.append("moved")
            except PositionOccupiedError:
                outcomes.append("occupied")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=move, args=(sample,)) for sample in samples]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(outcomes) == ["moved", "occupied"]
        assert Sample.objects.filter(storage_location=box, position="A1").count() == 1


class TestChangingCriticalFields:
    def test_a_change_needs_a_reason_and_records_it(self, editor, field_sample):
        services.change_sample(
            user=editor,
            sample=field_sample,
            status=SampleStatus.CONSUMED,
            reason="Used for extraction",
        )
        update_event = field_sample.events.filter(pgh_label="update").latest("pgh_id")

        assert update_event.status == SampleStatus.CONSUMED
        assert update_event.pgh_context.metadata["reason"] == "Used for extraction"
        with pytest.raises(ValueError, match="reason"):
            services.change_sample(user=editor, sample=field_sample, barcode="TUBE-9", reason="")

    def test_descriptive_fields_are_not_accepted_here_and_critical_ones_not_in_edit(
        self, editor, field_sample
    ):
        with pytest.raises(TypeError):
            services.change_sample(user=editor, sample=field_sample, notes="x", reason="r")
        with pytest.raises(TypeError):
            services.edit_sample(user=editor, sample=field_sample, status=SampleStatus.STORED)

    def test_negative_quantities_are_refused(self, editor, field_sample):
        with pytest.raises(ValidationError):
            services.change_sample(
                user=editor, sample=field_sample, volume_ul=Decimal("-1"), reason="r"
            )

    def test_edit_changes_descriptive_fields_without_a_reason(self, editor, field_sample):
        services.edit_sample(user=editor, sample=field_sample, symptom_notes="Vc, Lc", notes="Dry")

        assert Sample.objects.get(pk=field_sample.pk).symptom_notes == "Vc, Lc"

    def test_a_voided_sample_is_frozen(self, editor, field_sample):
        field_sample.void(user=editor, reason="Wrong tube")

        with pytest.raises(SampleServiceError, match="voided"):
            services.change_sample(
                user=editor, sample=field_sample, status=SampleStatus.STORED, reason="r"
            )


class TestQualityControl:
    def test_a_reading_records_who_measured(self, editor, field_sample):
        extract = services.derive_sample(user=editor, parent=field_sample, kind="dna_extract")

        reading = services.record_qc(
            user=editor,
            sample=extract,
            method=QcMethod.NANODROP,
            concentration_ng_ul=Decimal("87.5"),
            a260_280=Decimal("1.85"),
            outcome=QcOutcome.PASSED,
        )

        assert reading.measured_by == editor
        assert reading.sample == extract
        extract.refresh_from_db()
        assert extract.concentration_ng_ul is None

    def test_a_reading_can_be_applied_to_the_sample_with_an_automatic_reason(
        self, editor, field_sample
    ):
        extract = services.derive_sample(user=editor, parent=field_sample, kind="dna_extract")

        services.record_qc(
            user=editor,
            sample=extract,
            method=QcMethod.QUBIT,
            concentration_ng_ul=Decimal("12.3"),
            apply_to_sample=True,
        )
        extract.refresh_from_db()
        update_event = extract.events.filter(pgh_label="update").latest("pgh_id")

        assert extract.concentration_ng_ul == Decimal("12.300")
        assert "Qubit" in update_event.pgh_context.metadata["reason"]

    def test_a_reading_without_a_result_is_refused(self, editor, field_sample):
        with pytest.raises(ValidationError):
            services.record_qc(user=editor, sample=field_sample, method=QcMethod.GEL)


class TestSequences:
    def record(self, editor, sample, accession="PV947737"):
        return services.record_sequence(
            user=editor,
            sample=sample,
            accession=accession,
            length_bp=2828,
            organism="Sweet potato leaf curl virus",
            topology="circular",
            biosample_accession="SAMN57660677",
        )

    def test_a_genome_is_linked_to_its_sample(self, editor, field_sample):
        record = self.record(editor, field_sample)

        assert list(field_sample.sequence_records.all()) == [record]
        assert str(record) == "PV947737.1"
        assert record.ncbi_url.endswith("/nuccore/PV947737.1")

    def test_an_accession_is_unique_among_active_records(self, editor, field_sample):
        self.record(editor, field_sample)

        with pytest.raises(ValidationError):
            self.record(editor, field_sample)

    def test_a_viewer_cannot_link_a_genome(self, project, field_sample):
        viewer = member(project, ProjectRole.VIEWER)

        with pytest.raises(PermissionDenied):
            self.record(viewer, field_sample)

    def test_a_sample_with_sequences_cannot_be_voided_first(self, editor, field_sample):
        record = self.record(editor, field_sample)

        with pytest.raises(services.RecordInUseError):
            services.void_record(user=editor, record=field_sample, reason="mistake")

        services.void_record(user=editor, record=record, reason="wrong sample")
        services.void_record(user=editor, record=field_sample, reason="mistake")


class TestStorageLocations:
    def test_grids_are_limited_to_26_rows(self):
        manager = UserFactory(role=GlobalRole.PLATFORM_MANAGER)

        with pytest.raises(ValidationError):
            services.create_storage_location(
                user=manager, name="Huge", kind="box", grid_rows=27, grid_columns=5
            )

    def test_the_tree_is_built_through_parents(self):
        manager = UserFactory(role=GlobalRole.PLATFORM_MANAGER)
        room = services.create_storage_location(user=manager, name="LVBV", kind="room")
        freezer = services.create_storage_location(
            user=manager, name="-80", kind="freezer", parent=room, nominal_temperature_c=-80
        )

        assert freezer.path == "LVBV > -80"


class TestVoiding:
    def test_voiding_records_the_reason_and_the_actor(self, editor, field_sample):
        services.void_record(user=editor, record=field_sample, reason="Registered twice")
        void_event = field_sample.events.filter(pgh_label="update").latest("pgh_id")

        assert field_sample.is_voided
        assert void_event.pgh_context.metadata["reason"] == "Registered twice"
        assert void_event.pgh_context.metadata["user"] == str(editor.pk)

    def test_a_reason_is_required(self, editor, field_sample):
        with pytest.raises(ValueError, match="reason"):
            services.void_record(user=editor, record=field_sample, reason=" ")

    def test_a_sample_with_active_derivatives_cannot_be_voided(self, editor, field_sample):
        extract = services.derive_sample(user=editor, parent=field_sample, kind="dna_extract")

        with pytest.raises(RecordInUseError):
            services.void_record(user=editor, record=field_sample, reason="Wrong tube")
        services.void_record(user=editor, record=extract, reason="Contaminated")
        services.void_record(user=editor, record=field_sample, reason="Wrong tube")

    def test_an_event_with_active_samples_cannot_be_voided(self, editor, event, field_sample):
        with pytest.raises(RecordInUseError):
            services.void_record(user=editor, record=event, reason="Wrong date")
        services.void_record(user=editor, record=field_sample, reason="Wrong tube")
        services.void_record(user=editor, record=event, reason="Wrong date")

    def test_a_location_in_use_cannot_be_voided(self, editor, field_sample, box):
        manager = UserFactory(role=GlobalRole.PLATFORM_MANAGER)
        services.move_sample(
            user=editor, sample=field_sample, location=box, position="A1", reason="Banking"
        )

        with pytest.raises(RecordInUseError):
            services.void_record(user=manager, record=box, reason="Discarded")
        with pytest.raises(PermissionDenied):
            services.void_record(user=editor, record=box, reason="Discarded")

    def test_a_wrong_reading_can_be_voided_by_a_project_editor(self, editor, field_sample):
        reading = services.record_qc(
            user=editor, sample=field_sample, method=QcMethod.GEL, outcome=QcOutcome.FAILED
        )

        services.void_record(user=editor, record=reading, reason="Wrong lane")

        assert reading.is_voided
