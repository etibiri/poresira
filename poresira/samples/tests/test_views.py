# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.urls import reverse

from poresira.core.models import GlobalRole, ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.samples import services
from poresira.samples.models import (
    QcMeasurement,
    QcMethod,
    Sample,
    SampleKind,
    SampleStatus,
    StorageLocation,
)
from poresira.samples.tests.factories import (
    CollectionEventFactory,
    DerivativeFactory,
    SampleFactory,
    StorageLocationFactory,
)

pytestmark = pytest.mark.django_db


WRITE_ACTIONS = [
    "sample-edit",
    "sample-derive",
    "sample-move",
    "sample-change",
    "sample-qc",
    "sample-void",
]


def url(name, *args):
    return reverse(f"samples:{name}", args=args)


@pytest.fixture
def project():
    return ProjectFactory(code="spbf", name="Sweetpotato survey")


@pytest.fixture
def editor(project, client):
    user = ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user
    client.force_login(user)
    return user


@pytest.fixture
def sample(project):
    return SampleFactory(
        project=project, label="Tie_BFA266", symptom_notes="Vein-clearing", notes="Dry leaf"
    )


@pytest.fixture
def box():
    return StorageLocationFactory(name="DNA box 1", kind="box", grid_rows=9, grid_columns=9)


class TestAccess:
    PAGES = [
        ("sample-list", ()),
        ("event-list", ()),
        ("event-create", ()),
        ("storage-tree", ()),
        ("storage-create", ()),
    ]

    @pytest.mark.parametrize(("name", "args"), PAGES)
    def test_anonymous_users_are_sent_to_login(self, client, name, args):
        response = client.get(url(name, *args))

        assert response.status_code == 302
        assert response.url.startswith(reverse("login"))

    @pytest.mark.parametrize(
        "action", ["sample-detail", "sample-edit", "sample-derive", "sample-move", "sample-qc"]
    )
    def test_anonymous_users_cannot_reach_a_sample(self, client, sample, action):
        assert client.get(url(action, sample.pk)).status_code == 302

    @pytest.mark.parametrize(
        "action",
        [
            "sample-edit",
            "sample-derive",
            "sample-move",
            "sample-change",
            "sample-qc",
            "sample-void",
        ],
    )
    def test_a_sample_of_a_foreign_project_is_not_found(self, client, editor, action):
        foreign = SampleFactory()

        assert client.get(url(action, foreign.pk)).status_code == 404
        assert client.get(url("sample-detail", foreign.pk)).status_code == 404

    @pytest.mark.parametrize(
        "action",
        [
            "sample-edit",
            "sample-derive",
            "sample-move",
            "sample-change",
            "sample-qc",
            "sample-void",
        ],
    )
    @pytest.mark.parametrize(
        ("project_role", "global_role"),
        [(ProjectRole.VIEWER, GlobalRole.TECHNICIAN), (ProjectRole.LEAD, GlobalRole.GUEST)],
    )
    def test_read_only_members_can_see_but_not_change(
        self, client, sample, action, project_role, global_role
    ):
        reader = ProjectMembershipFactory(
            project=sample.project, role=project_role, user=UserFactory(role=global_role)
        ).user
        client.force_login(reader)

        assert client.get(url("sample-detail", sample.pk)).status_code == 200
        assert client.get(url(action, sample.pk)).status_code == 403
        assert client.post(url(action, sample.pk), {"reason": "x"}).status_code == 403

    def test_buttons_only_appear_for_people_who_can_use_them(self, client, editor, sample):
        assert url("sample-move", sample.pk) in client.get(url("sample-detail", sample.pk)).text

        reader = ProjectMembershipFactory(project=sample.project, role=ProjectRole.VIEWER).user
        client.force_login(reader)

        assert url("sample-move", sample.pk) not in client.get(url("sample-detail", sample.pk)).text

    def test_a_voided_sample_cannot_be_changed(self, client, editor, sample):
        services.void_record(user=editor, record=sample, reason="Registered twice")

        page = client.get(url("sample-detail", sample.pk)).text
        assert "Registered twice" in page
        assert url("sample-move", sample.pk) not in page
        assert client.get(url("sample-move", sample.pk)).status_code == 403


class TestSampleList:
    @pytest.fixture
    def samples(self, project):
        field = SampleFactory(project=project, label="Tie_BFA266")
        extract = DerivativeFactory(parent=field, kind=SampleKind.DNA_EXTRACT, label="Tie_BFA266")
        return field, extract, SampleFactory(project=project, label="Kan_BFA160")

    def test_only_visible_samples_are_listed(self, client, editor, samples):
        SampleFactory(label="Secret")

        page = client.get(url("sample-list")).text

        assert "Tie_BFA266" in page
        assert "Secret" not in page

    def test_filters_combine(self, client, editor, samples):
        field, extract, other = samples

        assert other.identifier not in client.get(url("sample-list"), {"q": "BFA266"}).text
        by_kind = client.get(url("sample-list"), {"kind": "dna_extract"}).text
        assert extract.identifier in by_kind
        assert field.identifier not in by_kind
        assert other.identifier in client.get(url("sample-list"), {"q": other.identifier}).text

    def test_status_and_project_filters(self, client, editor, project, samples):
        services.change_sample(
            user=editor, sample=samples[2], status=SampleStatus.CONSUMED, reason="Used"
        )

        consumed = client.get(url("sample-list"), {"status": "consumed"}).text
        assert samples[2].identifier in consumed
        assert samples[0].identifier not in consumed
        in_project = client.get(url("sample-list"), {"project": project.pk}).text
        assert samples[0].identifier in in_project

    def test_voided_samples_are_hidden_unless_asked_for(self, client, editor, samples):
        services.void_record(user=editor, record=samples[2], reason="Wrong tube")

        assert samples[2].identifier not in client.get(url("sample-list")).text
        shown = client.get(url("sample-list"), {"include_voided": "on"}).text
        assert samples[2].identifier in shown

    def test_htmx_requests_get_only_the_results(self, client, editor, samples):
        response = client.get(url("sample-list"), {"q": "BFA160"}, HTTP_HX_REQUEST="true")

        assert "<html" not in response.text
        assert samples[2].identifier in response.text
        assert samples[0].identifier not in response.text

    def test_results_are_paginated_and_filters_survive_paging(self, client, editor, project):
        event = CollectionEventFactory(project=project)
        for number in range(55):
            SampleFactory(project=project, collection_event=event, label=f"PAGE-{number:03d}")

        first = client.get(url("sample-list"), {"q": "PAGE"}).text
        second = client.get(url("sample-list"), {"q": "PAGE", "page": 2}).text

        assert "Page 1 of 2 (55 samples)" in first
        assert "q=PAGE&amp;page=2" in first or "q=PAGE&page=2" in first
        assert second.count("PAGE-") == 5


class TestSampleDetail:
    def test_it_shows_attributes_lineage_measurements_and_history(self, client, editor, sample):
        extract = services.derive_sample(user=editor, parent=sample, kind="dna_extract")
        services.record_qc(
            user=editor, sample=extract, method=QcMethod.NANODROP, concentration_ng_ul=42
        )
        services.move_sample(
            user=editor,
            sample=sample,
            location=StorageLocationFactory(name="Biobank cabinet"),
            reason="Banking",
        )

        page = client.get(url("sample-detail", sample.pk)).text

        assert "Vein-clearing" in page
        assert extract.identifier in page  # lineage
        assert "Biobank cabinet" in page
        assert "Banking" in page  # history reason
        assert "NanoDrop" in client.get(url("sample-detail", extract.pk)).text

    def test_the_lineage_marks_the_current_sample(self, client, editor, sample):
        extract = services.derive_sample(user=editor, parent=sample, kind="dna_extract")

        page = client.get(url("sample-detail", extract.pk)).text

        assert "▶" in page
        assert sample.identifier in page


class TestEditing:
    def test_edit_description(self, client, editor, sample):
        response = client.post(
            url("sample-edit", sample.pk),
            {"label": "Tie_BFA266", "host_taxon": "Ipomoea batatas", "notes": "Re-dried"},
        )

        assert response.status_code == 302
        assert Sample.objects.get(pk=sample.pk).notes == "Re-dried"

    def test_invalid_input_comes_back_with_the_error(self, client, editor, sample):
        response = client.post(url("sample-edit", sample.pk), {"label": "x", "symptom_score": "12"})

        assert response.status_code == 200
        assert Sample.objects.get(pk=sample.pk).symptom_score is None


class TestDeriving:
    def test_only_allowed_kinds_are_offered(self, client, editor, sample):
        page = client.get(url("sample-derive", sample.pk)).text

        assert "DNA extract" in page
        assert "Amplification product" not in page

    def test_creates_a_derivative_and_opens_it(self, client, editor, sample):
        response = client.post(
            url("sample-derive", sample.pk),
            {"kind": "dna_extract", "label": "Tie_BFA266", "volume_ul": "50"},
        )

        extract = Sample.objects.get(parent=sample)
        assert response.url == url("sample-detail", extract.pk)
        assert (extract.kind, extract.volume_ul) == (SampleKind.DNA_EXTRACT, 50)
        assert extract.project == sample.project

    def test_a_disallowed_kind_is_refused_by_the_form(self, client, editor, sample):
        response = client.post(url("sample-derive", sample.pk), {"kind": "amplification_product"})

        assert response.status_code == 200
        assert not Sample.objects.filter(parent=sample).exists()


class TestMoving:
    def test_a_move_needs_a_reason_and_leaves_the_sample_alone_without_one(
        self, client, editor, sample, box
    ):
        response = client.post(
            url("sample-move", sample.pk), {"location": box.pk, "position": "A1", "reason": ""}
        )

        assert response.status_code == 200
        assert Sample.objects.get(pk=sample.pk).storage_location is None

    def test_a_move_is_saved_and_its_reason_reaches_the_history(self, client, editor, sample, box):
        response = client.post(
            url("sample-move", sample.pk),
            {"location": box.pk, "position": "a1", "reason": "Banked after extraction"},
        )
        moved = Sample.objects.get(pk=sample.pk)

        assert response.status_code == 302
        assert (moved.storage_location, moved.position) == (box, "A1")
        assert "Banked after extraction" in client.get(url("sample-detail", sample.pk)).text

    def test_rule_violations_are_shown_in_the_form(self, client, editor, sample, box):
        other = SampleFactory(project=sample.project)
        services.move_sample(user=editor, sample=other, location=box, position="A1", reason="x")

        taken = client.post(
            url("sample-move", sample.pk),
            {"location": box.pk, "position": "A1", "reason": "Banking"},
        )
        outside = client.post(
            url("sample-move", sample.pk),
            {"location": box.pk, "position": "Z9", "reason": "Banking"},
        )

        assert "already holds" in taken.text
        assert "outside" in outside.text

    def test_locations_are_offered_with_their_full_path(self, client, editor, sample, box):
        room = StorageLocationFactory(name="LVBV", kind="room")
        StorageLocationFactory(name="Freezer", parent=room)

        assert "LVBV &gt; Freezer" in client.get(url("sample-move", sample.pk)).text


class TestChangingAndMeasuring:
    def test_status_and_quantities(self, client, editor, sample):
        response = client.post(
            url("sample-change", sample.pk),
            {
                "status": "consumed",
                "barcode": "TUBE-1",
                "concentration_ng_ul": "12.5",
                "volume_ul": "",
                "reason": "Used for extraction",
            },
        )
        changed = Sample.objects.get(pk=sample.pk)

        assert response.status_code == 302
        assert (changed.status, changed.barcode) == ("consumed", "TUBE-1")
        assert str(changed.concentration_ng_ul) == "12.500"
        assert changed.volume_ul is None

    def test_a_reason_is_required(self, client, editor, sample):
        response = client.post(
            url("sample-change", sample.pk), {"status": "consumed", "reason": "  "}
        )

        assert response.status_code == 200
        assert Sample.objects.get(pk=sample.pk).status == SampleStatus.REGISTERED

    def test_a_measurement_can_be_recorded_and_applied(self, client, editor, sample):
        response = client.post(
            url("sample-qc", sample.pk),
            {
                "method": "qubit",
                "concentration_ng_ul": "8.25",
                "outcome": "passed",
                "apply_to_sample": "on",
            },
        )

        assert response.status_code == 302
        reading = QcMeasurement.objects.get(sample=sample)
        assert (reading.measured_by, reading.method) == (editor, "qubit")
        assert str(Sample.objects.get(pk=sample.pk).concentration_ng_ul) == "8.250"

    def test_an_empty_measurement_is_refused(self, client, editor, sample):
        response = client.post(
            url("sample-qc", sample.pk), {"method": "gel", "outcome": "not_assessed"}
        )

        assert response.status_code == 200
        assert not QcMeasurement.objects.exists()

    def test_a_wrong_measurement_can_be_voided(self, client, editor, sample):
        reading = services.record_qc(
            user=editor, sample=sample, method=QcMethod.GEL, outcome="failed"
        )

        response = client.post(url("qc-void", reading.pk), {"reason": "Wrong lane"})

        assert response.status_code == 302
        assert QcMeasurement.objects.get(pk=reading.pk).is_voided


class TestVoiding:
    def test_void_with_a_reason(self, client, editor, sample):
        response = client.post(url("sample-void", sample.pk), {"reason": "Registered twice"})

        assert response.status_code == 302
        assert Sample.objects.get(pk=sample.pk).void_reason == "Registered twice"

    def test_a_sample_with_derivatives_cannot_be_voided(self, client, editor, sample):
        services.derive_sample(user=editor, parent=sample, kind="dna_extract")

        response = client.post(url("sample-void", sample.pk), {"reason": "Wrong tube"})

        assert response.status_code == 200
        assert "Void the derivatives" in response.text
        assert not Sample.objects.get(pk=sample.pk).is_voided


class TestEvents:
    def test_list_and_detail(self, client, editor, project, sample):
        event = sample.collection_event

        assert event.identifier in client.get(url("event-list")).text
        detail = client.get(url("event-detail", event.pk)).text
        assert sample.identifier in detail
        assert url("field-sample-create", event.pk) in detail

    def test_events_of_other_projects_are_invisible(self, client, editor):
        foreign = CollectionEventFactory()

        assert client.get(url("event-detail", foreign.pk)).status_code == 404
        assert foreign.identifier not in client.get(url("event-list")).text

    def test_creating_an_event(self, client, editor, project):
        response = client.post(
            url("event-create"),
            {
                "project": project.pk,
                "collected_on": "2016-04-15",
                "province": "Nahouri",
                "locality": "Tiebele",
                "latitude": "11.12",
                "longitude": "-0.96",
            },
        )

        assert response.status_code == 302
        assert Sample.objects.count() == 0
        assert project.collection_events.get().locality == "Tiebele"

    def test_only_editable_projects_can_be_chosen(self, client, editor, project):
        foreign = ProjectFactory()

        response = client.post(url("event-create"), {"project": foreign.pk, "locality": "x"})

        assert response.status_code == 200
        assert not foreign.collection_events.exists()

    def test_registering_a_field_sample(self, client, editor, project):
        event = CollectionEventFactory(project=project)

        response = client.post(
            url("field-sample-create", event.pk),
            {"label": "BFA1", "host_taxon": "Ipomoea batatas", "symptom_score": "4"},
        )

        created = Sample.objects.get(label="BFA1")
        assert response.url == url("sample-detail", created.pk)
        assert created.collection_event == event

    def test_readers_cannot_register_samples(self, client, project):
        event = CollectionEventFactory(project=project)
        reader = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user
        client.force_login(reader)

        assert client.get(url("field-sample-create", event.pk)).status_code == 403


class TestStorage:
    def test_the_tree_lists_locations_with_their_sample_counts(self, client, editor, sample, box):
        services.move_sample(user=editor, sample=sample, location=box, position="A1", reason="x")

        page = client.get(url("storage-tree")).text

        assert "DNA box 1" in page
        assert "1 sample" in page
        assert "9 \u00d7 9" in page

    def test_only_platform_staff_see_and_use_the_creation_form(self, client, editor):
        assert url("storage-create") not in client.get(url("storage-tree")).text
        assert client.get(url("storage-create")).status_code == 403

        manager = UserFactory(role=GlobalRole.PLATFORM_MANAGER)
        client.force_login(manager)
        response = client.post(
            url("storage-create"),
            {"name": "Freezer -20", "kind": "freezer", "nominal_temperature_c": "-20"},
        )

        assert response.status_code == 302
        assert StorageLocation.objects.get(name="Freezer -20").nominal_temperature_c == -20

    def test_a_duplicate_location_is_an_error_not_a_crash(self, client):
        StorageLocationFactory(name="Freezer -20")
        client.force_login(UserFactory(role=GlobalRole.PLATFORM_MANAGER))

        response = client.post(url("storage-create"), {"name": "Freezer -20", "kind": "freezer"})

        assert response.status_code == 200


class TestFrench:
    def test_the_interface_follows_the_user_language(self, client, editor, sample):
        client.post(reverse("set-language"), {"language": "fr", "next": "/"})

        assert "Échantillons" in client.get(url("sample-list")).text
        detail = client.get(url("sample-detail", sample.pk)).text
        assert "Lignée" in detail
        assert "Historique" in detail
