# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from poresira.core.audit import verify_audit_chain, with_reason
from poresira.core.identifiers import TYPE_CODE_PATTERN, is_valid_identifier
from poresira.core.models import AuditChainLink
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.core.voiding import HardDeleteForbiddenError
from poresira.samples.models import (
    COLLECTION_EVENT_TYPE_CODE,
    KIND_TYPE_CODES,
    CollectionEvent,
    QcMeasurement,
    QcOutcome,
    Sample,
    SampleKind,
    SampleStatus,
    StorageLocation,
)
from poresira.samples.tests.factories import (
    CollectionEventFactory,
    DerivativeFactory,
    QcMeasurementFactory,
    SampleFactory,
    StorageLocationFactory,
)

pytestmark = pytest.mark.django_db


def assert_database_refuses(error, action):
    with pytest.raises(error), transaction.atomic():
        action()


class TestIdentifiers:
    def test_type_codes_are_unique_and_valid(self):
        codes = [COLLECTION_EVENT_TYPE_CODE, *KIND_TYPE_CODES.values()]

        assert len(set(codes)) == len(codes)
        assert all(TYPE_CODE_PATTERN.match(code) for code in codes)
        assert set(KIND_TYPE_CODES) == set(SampleKind)

    @pytest.mark.parametrize("kind", list(SampleKind))
    def test_each_kind_gets_its_own_type_letter(self, kind):
        parent = SampleFactory()
        sample = (
            parent
            if kind == SampleKind.FIELD_SAMPLE
            else DerivativeFactory(parent=parent, kind=kind)
        )

        assert sample.identifier.split("-")[1].startswith(KIND_TYPE_CODES[kind])
        assert is_valid_identifier(sample.identifier)

    def test_collection_events_are_identified_with_c(self):
        event = CollectionEventFactory()

        assert event.identifier.split("-")[1].startswith("C")
        assert is_valid_identifier(event.identifier)

    def test_identifier_is_never_reassigned(self):
        sample = SampleFactory()
        first_identifier = sample.identifier
        sample.notes = "edited"
        sample.save()

        assert Sample.objects.get(pk=sample.pk).identifier == first_identifier
        assert SampleFactory().identifier != first_identifier


class TestLineage:
    def test_a_field_sample_has_no_parent_and_a_derivative_needs_one(self):
        field_sample = SampleFactory()

        assert_database_refuses(
            IntegrityError,
            lambda: SampleFactory(
                parent=field_sample,
                project=field_sample.project,
                collection_event=field_sample.collection_event,
            ),
        )
        assert_database_refuses(
            IntegrityError,
            lambda: SampleFactory(kind=SampleKind.DNA_EXTRACT),
        )

    def test_a_derivative_must_share_project_and_collection_event_with_its_parent(self):
        parent = SampleFactory()

        with pytest.raises(ValueError, match="parent"):
            DerivativeFactory(
                parent=parent, project=ProjectFactory(), collection_event=parent.collection_event
            )
        with pytest.raises(ValueError, match="parent"):
            DerivativeFactory(
                parent=parent,
                project=parent.project,
                collection_event=CollectionEventFactory(project=parent.project),
            )

    @pytest.mark.parametrize(
        "field_name", ["identifier", "kind", "parent", "project", "collection_event"]
    )
    def test_identity_fields_cannot_change(self, field_name):
        derivative = DerivativeFactory()
        other = DerivativeFactory()
        new_values = {
            "identifier": "WBF-D26-99999-0",
            "kind": SampleKind.RNA_EXTRACT,
            "parent": other.parent,
            "project": other.project,
            "collection_event": other.collection_event,
        }

        assert_database_refuses(
            DatabaseError,
            lambda: Sample.objects.filter(pk=derivative.pk).update(
                **{field_name: new_values[field_name]}
            ),
        )


class TestCriticalFields:
    @pytest.mark.parametrize(
        ("field_name", "new_value"),
        [
            ("concentration_ng_ul", 12),
            ("volume_ul", 30),
            ("status", SampleStatus.STORED),
            ("barcode", "TUBE-0001"),
            ("position", ""),
        ],
    )
    def test_changing_requires_a_reason(self, field_name, new_value):
        sample = SampleFactory()
        if field_name == "position":
            location = StorageLocationFactory()
            with with_reason("Initial placement"):
                Sample.objects.filter(pk=sample.pk).update(storage_location=location, position="A1")
            sample.refresh_from_db()
        setattr(sample, field_name, new_value)

        assert_database_refuses(IntegrityError, sample.save)
        with with_reason("Checked on the bench"):
            sample.save()

    def test_changing_the_storage_location_requires_a_reason(self):
        sample = SampleFactory()
        sample.storage_location = StorageLocationFactory()

        assert_database_refuses(IntegrityError, sample.save)
        with with_reason("Moved to the -80 freezer"):
            sample.save()

    def test_other_fields_change_freely(self):
        sample = SampleFactory()
        sample.notes = "Leaf dried at 37 °C"
        sample.label = "BFA-renamed"
        sample.symptom_notes = "Vc, Lc"
        sample.save()

        assert Sample.objects.get(pk=sample.pk).notes == "Leaf dried at 37 °C"

    def test_the_reason_is_written_to_the_audit_entry(self):
        sample = SampleFactory()
        sample.status = SampleStatus.STORED
        with with_reason("Put in the box"):
            sample.save()

        update_event = sample.events.get(pgh_label="update")

        assert update_event.pgh_context.metadata["reason"] == "Put in the box"
        assert update_event.status == SampleStatus.STORED


class TestStoragePositions:
    @pytest.fixture
    def box(self):
        return StorageLocationFactory(name="Box 1", kind="box", grid_rows=9, grid_columns=9)

    def place(self, sample, box, position):
        sample.storage_location, sample.position = box, position
        with with_reason("Placed in the box"):
            sample.save()

    def test_a_position_needs_a_location(self):
        assert_database_refuses(IntegrityError, lambda: SampleFactory(position="A1"))

    def test_two_active_samples_cannot_share_a_position(self, box):
        self.place(SampleFactory(), box, "A1")

        assert_database_refuses(IntegrityError, lambda: self.place(SampleFactory(), box, "A1"))

    def test_the_same_position_in_another_box_is_fine(self, box):
        self.place(SampleFactory(), box, "A1")
        self.place(SampleFactory(), StorageLocationFactory(name="Box 2", kind="box"), "A1")

    def test_a_position_is_free_again_once_the_sample_is_consumed(self, box):
        first = SampleFactory()
        self.place(first, box, "A1")
        first.status = SampleStatus.CONSUMED
        with with_reason("Used up"):
            first.save()

        self.place(SampleFactory(), box, "A1")

    def test_a_position_is_free_again_once_the_sample_is_voided(self, box):
        first = SampleFactory()
        self.place(first, box, "A1")
        first.void(user=UserFactory(), reason="Registered twice")

        self.place(SampleFactory(), box, "A1")


class TestUniqueness:
    def test_label_is_unique_per_project_and_kind_among_active_samples(self):
        sample = SampleFactory(label="BFA266")

        assert_database_refuses(
            IntegrityError,
            lambda: SampleFactory(
                project=sample.project,
                collection_event=sample.collection_event,
                label="BFA266",
            ),
        )
        SampleFactory(label="BFA266")  # another project
        DerivativeFactory(parent=sample, label="BFA266")  # another kind

    def test_a_voided_label_can_be_reused(self):
        sample = SampleFactory(label="BFA266")
        sample.void(user=UserFactory(), reason="Wrong tube")

        SampleFactory(
            project=sample.project, collection_event=sample.collection_event, label="BFA266"
        )

    def test_barcode_is_unique_but_blank_barcodes_repeat(self):
        sample = SampleFactory()
        sample.barcode = "TUBE-1"
        with with_reason("Labelled"):
            sample.save()

        assert_database_refuses(IntegrityError, lambda: SampleFactory(barcode="TUBE-1"))
        SampleFactory()
        SampleFactory()

    def test_symptom_score_is_between_1_and_9(self):
        assert SampleFactory(symptom_score=9).symptom_score == 9
        for invalid_score in (0, 10):
            assert_database_refuses(
                IntegrityError, lambda s=invalid_score: SampleFactory(symptom_score=s)
            )

    def test_concentration_and_volume_cannot_be_negative(self):
        assert_database_refuses(IntegrityError, lambda: SampleFactory(concentration_ng_ul=-1))
        assert_database_refuses(IntegrityError, lambda: SampleFactory(volume_ul=-1))


class TestVoidingAndAudit:
    def test_samples_cannot_be_deleted_in_python_or_sql(self):
        sample = SampleFactory()

        with pytest.raises(HardDeleteForbiddenError):
            sample.delete()

        def raw_delete():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM samples_sample")

        assert_database_refuses(DatabaseError, raw_delete)

    def test_voiding_records_the_reason(self):
        sample = SampleFactory()
        sample.void(user=UserFactory(), reason="Contaminated")

        assert Sample.objects.get(pk=sample.pk).is_voided
        assert not Sample.objects.active().filter(pk=sample.pk).exists()

    def test_every_model_writes_audit_entries_into_the_chain(self):
        sample = SampleFactory()
        QcMeasurementFactory(sample=DerivativeFactory(parent=sample))
        StorageLocationFactory()
        sample.notes = "edited"
        sample.save()

        report = verify_audit_chain()

        assert report.is_intact
        chained_tables = set(AuditChainLink.objects.values_list("event_table", flat=True))
        assert chained_tables >= {
            "core_projectevent",
            "samples_collectioneventhistory",
            "samples_sampleevent",
            "samples_qcmeasurementevent",
            "samples_storagelocationevent",
        }
        assert sample.events.count() == 2


class TestCollectionEvent:
    def test_the_date_may_be_unknown(self):
        assert CollectionEventFactory(collected_on=None).collected_on is None

    def test_coordinates_come_in_pairs_and_stay_in_range(self):
        CollectionEventFactory(latitude="11.08", longitude="-0.9")
        assert_database_refuses(IntegrityError, lambda: CollectionEventFactory(latitude="11.08"))
        assert_database_refuses(
            IntegrityError, lambda: CollectionEventFactory(latitude="91", longitude="0")
        )
        assert_database_refuses(
            IntegrityError, lambda: CollectionEventFactory(latitude="0", longitude="181")
        )

    def test_identifier_and_project_cannot_change(self):
        event = CollectionEventFactory()

        assert_database_refuses(
            DatabaseError,
            lambda: CollectionEvent.objects.filter(pk=event.pk).update(project=ProjectFactory()),
        )
        assert_database_refuses(
            DatabaseError,
            lambda: CollectionEvent.objects.filter(pk=event.pk).update(identifier="WBF-C26-1-0"),
        )


class TestStorageLocation:
    def test_names_are_unique_per_parent_including_the_root(self):
        room = StorageLocationFactory(name="Room A", kind="room")
        assert_database_refuses(
            IntegrityError, lambda: StorageLocationFactory(name="Room A", kind="room")
        )
        StorageLocationFactory(name="Freezer 1", parent=room)
        assert_database_refuses(
            IntegrityError, lambda: StorageLocationFactory(name="Freezer 1", parent=room)
        )
        StorageLocationFactory(name="Freezer 1", parent=StorageLocationFactory(name="Room B"))

    def test_grid_needs_both_dimensions(self):
        StorageLocationFactory(kind="box", grid_rows=9, grid_columns=9)
        assert_database_refuses(
            IntegrityError, lambda: StorageLocationFactory(kind="box", grid_rows=9)
        )
        assert_database_refuses(
            IntegrityError, lambda: StorageLocationFactory(kind="box", grid_rows=0, grid_columns=9)
        )

    def test_the_parent_cannot_change_and_path_follows_the_tree(self):
        room = StorageLocationFactory(name="LVBV", kind="room")
        freezer = StorageLocationFactory(name="Freezer -20", parent=room)
        box = StorageLocationFactory(name="Box 3", parent=freezer, kind="box")

        assert box.path == "LVBV > Freezer -20 > Box 3"
        assert_database_refuses(
            DatabaseError,
            lambda: StorageLocation.objects.filter(pk=box.pk).update(parent=room),
        )


class TestQcMeasurement:
    def test_a_reading_needs_a_result(self):
        assert_database_refuses(
            IntegrityError,
            lambda: QcMeasurementFactory(concentration_ng_ul=None),
        )
        QcMeasurementFactory(concentration_ng_ul=None, outcome=QcOutcome.FAILED)

    def test_values_are_in_range(self):
        assert_database_refuses(
            IntegrityError, lambda: QcMeasurementFactory(concentration_ng_ul=-1)
        )
        assert_database_refuses(IntegrityError, lambda: QcMeasurementFactory(integrity_number=11))

    def test_a_reading_cannot_be_edited_but_its_notes_can(self):
        reading = QcMeasurementFactory()

        assert_database_refuses(
            DatabaseError,
            lambda: QcMeasurement.objects.filter(pk=reading.pk).update(concentration_ng_ul=99),
        )
        reading.notes = "Measured twice"
        reading.save()

    def test_a_wrong_reading_is_voided_with_a_reason(self):
        reading = QcMeasurementFactory()
        reading.void(user=UserFactory(), reason="Wrong sample loaded")

        assert not QcMeasurement.objects.active().filter(pk=reading.pk).exists()


class TestVisibility:
    def test_members_see_the_samples_events_and_readings_of_their_projects_only(self):
        member = UserFactory()
        reading = QcMeasurementFactory()
        own_project = reading.sample.project
        ProjectMembershipFactory(project=own_project, user=member)
        foreign_reading = QcMeasurementFactory()

        assert list(QcMeasurement.objects.visible_to(member)) == [reading]
        assert foreign_reading not in QcMeasurement.objects.visible_to(member)
        assert set(Sample.objects.visible_to(member)) == {reading.sample, reading.sample.parent}
        assert not Sample.objects.visible_to(UserFactory()).exists()
