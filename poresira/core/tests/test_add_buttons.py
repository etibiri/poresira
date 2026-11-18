# SPDX-License-Identifier: AGPL-3.0-or-later
"""The buttons that add things from the list pages, and the forms behind them."""

import pytest
from django.urls import reverse

from poresira.core.models import GlobalRole, ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.deposits.models import PublicAccession
from poresira.pcr.models import Assay, CyclingProgram, Primer, PrimerDirection
from poresira.pcr.tests.factories import CyclingProgramFactory, PrimerFactory
from poresira.samples.models import SequenceRecord
from poresira.samples.tests.factories import CollectionEventFactory, SampleFactory
from poresira.sequencing.models import FlowCellType, Instrument, LibraryKit
from poresira.sequencing.tests.factories import LibraryFactory, SequencingRunFactory
from poresira.storage.models import StorageBackend

pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return ProjectFactory(code="spbf")


@pytest.fixture
def editor(project, client):
    user = ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user
    client.force_login(user)
    return user


@pytest.fixture
def manager(client):
    user = UserFactory(role=GlobalRole.PLATFORM_MANAGER)
    client.force_login(user)
    return user


@pytest.fixture
def guest(client):
    user = UserFactory(role=GlobalRole.GUEST)
    client.force_login(user)
    return user


LIST_PAGES = [
    ("samples:sample-list", "samples:sample-new"),
    ("sequencing:run-list", "sequencing:run-new"),
    ("deposits:deposit-list", "deposits:deposit-pick"),
    ("computing:job-list", "computing:job-pick"),
]


class TestPickButtons:
    @pytest.mark.parametrize(("page", "target"), LIST_PAGES)
    def test_an_editor_sees_the_button(self, client, editor, page, target):
        assert reverse(target) in client.get(reverse(page)).content.decode()

    @pytest.mark.parametrize(("page", "target"), LIST_PAGES)
    def test_a_guest_does_not(self, client, guest, page, target):
        assert reverse(target) not in client.get(reverse(page)).content.decode()

    def test_the_sample_picker_goes_to_the_field_sample_form(self, client, editor, project):
        event = CollectionEventFactory(project=project)
        response = client.post(reverse("samples:sample-new"), {"parent": event.pk})
        assert response.url == reverse("samples:field-sample-create", args=[event.pk])

    def test_the_run_picker_goes_to_the_run_plan(self, client, editor, project):
        library = LibraryFactory(project=project)
        response = client.post(reverse("sequencing:run-new"), {"parent": library.pk})
        assert response.url == reverse("sequencing:run-plan", args=[library.pk])

    def test_the_deposit_picker_goes_to_the_deposit_form(self, client, editor, project):
        run = SequencingRunFactory(library__project=project)
        response = client.post(reverse("deposits:deposit-pick"), {"parent": run.pk})
        assert response.url == reverse("deposits:deposit-new", args=[run.pk])

    def test_the_job_picker_also_asks_for_the_kind(self, client, editor, project):
        run = SequencingRunFactory(library__project=project)
        response = client.post(
            reverse("computing:job-pick"), {"parent": run.pk, "kind": "basecalling"}
        )
        assert response.url == reverse("computing:job-new", args=[run.pk, "basecalling"])

    def test_only_the_parents_of_editable_projects_are_offered(self, client, editor, project):
        other = CollectionEventFactory(project=ProjectFactory(code="other"))
        response = client.post(reverse("samples:sample-new"), {"parent": other.pk})
        assert response.status_code == 200  # the form comes back with an error


class TestReferenceForms:
    def test_a_manager_adds_a_kit_a_flow_cell_type_and_an_instrument(self, client, manager):
        client.post(
            reverse("sequencing:kit-new"),
            {
                "code": "SQK-NBD114.96",
                "name": "Native",
                "chemistry": "native_barcoding",
                "barcode_count": 96,
            },
        )
        client.post(
            reverse("sequencing:flow-cell-type-new"),
            {"code": "FLO-MIN114", "name": "MinION R10.4.1", "pore": "R10.4.1", "reusable": "on"},
        )
        client.post(reverse("sequencing:instrument-new"), {"name": "Mk1C", "model": "MinION"})
        assert LibraryKit.objects.filter(code="SQK-NBD114.96").exists()
        assert FlowCellType.objects.get(code="FLO-MIN114").reusable
        assert Instrument.objects.filter(name="Mk1C").exists()

    def test_a_flow_cell_type_can_be_single_use(self, client, manager):
        client.post(
            reverse("sequencing:flow-cell-type-new"),
            {"code": "FLO-FLG114", "name": "Flongle"},
        )
        assert not FlowCellType.objects.get(code="FLO-FLG114").reusable

    @pytest.mark.parametrize(
        "name",
        [
            "sequencing:kit-new",
            "sequencing:flow-cell-type-new",
            "sequencing:instrument-new",
            "storage:backend-new",
            "pcr:primer-new",
            "pcr:program-new",
            "pcr:assay-new",
            "pcr:master-mix-new",
        ],
    )
    def test_a_project_editor_is_refused(self, client, editor, name):
        assert client.get(reverse(name)).status_code == 403

    def test_a_manager_adds_a_storage_backend(self, client, manager, tmp_path):
        client.post(
            reverse("storage:backend-new"),
            {"name": "Lab NAS", "kind": "nas", "root": str(tmp_path)},
        )
        assert StorageBackend.objects.get(name="Lab NAS").root == str(tmp_path)

    def test_a_backend_directory_must_exist(self, client, manager):
        response = client.post(
            reverse("storage:backend-new"),
            {"name": "Ghost", "kind": "local", "root": "/no/such/directory"},
        )
        assert response.status_code == 200
        assert not StorageBackend.objects.filter(name="Ghost").exists()

    def test_the_forms_that_need_reference_data_say_where_to_add_it(self, client, editor):
        for name in ("sequencing:library-create", "sequencing:flow-cell-register"):
            page = client.get(reverse(name)).content.decode()
            assert reverse("sequencing:reference") in page

    def test_a_barcoding_kit_needs_its_barcode_count(self, client, manager):
        response = client.post(
            reverse("sequencing:kit-new"),
            {"code": "SQK-NBD114.96", "name": "Native", "chemistry": "native_barcoding"},
        )
        assert response.status_code == 200
        assert not LibraryKit.objects.exists()


class TestPcrReferenceForms:
    def test_a_manager_adds_a_primer(self, client, manager):
        client.post(
            reverse("pcr:primer-new"),
            {"name": "SPFMV-F", "sequence": "ATGGCTGATAGC", "direction": "forward"},
        )
        assert Primer.objects.filter(name="SPFMV-F", sequence="ATGGCTGATAGC").exists()

    STEPS = "initial;94;180;Denaturation\ncycle;55;30;Annealing\nfinal;72;300"

    def test_a_manager_adds_a_cycling_program(self, client, manager):
        response = client.post(
            reverse("pcr:program-new"), {"name": "SPFMV", "cycles": 35, "steps": self.STEPS}
        )
        assert response.status_code == 302
        steps = list(CyclingProgram.objects.get(name="SPFMV").steps.order_by("order"))
        assert [step.stage for step in steps] == ["initial", "cycle", "final"]
        assert steps[1].label == "Annealing"
        assert steps[2].label == ""

    @pytest.mark.parametrize("steps", ["cycle;hot;30", "bake;94;30", "cycle;94", "initial;94;30"])
    def test_a_bad_step_is_reported(self, client, manager, steps):
        response = client.post(
            reverse("pcr:program-new"), {"name": "Bad", "cycles": 35, "steps": steps}
        )
        assert response.status_code == 200
        assert not CyclingProgram.objects.filter(name="Bad").exists()

    def test_a_manager_adds_an_assay_from_the_right_primers(self, client, manager):
        forward = PrimerFactory(direction=PrimerDirection.FORWARD)
        reverse_primer = PrimerFactory(direction=PrimerDirection.REVERSE)
        program = CyclingProgramFactory()
        form = {
            "name": "SPFMV CP",
            "target": "SPFMV coat protein",
            "forward_primer": forward.pk,
            "reverse_primer": reverse_primer.pk,
            "cycling_program": program.pk,
        }
        assert client.post(reverse("pcr:assay-new"), form).status_code == 302
        assert Assay.objects.filter(name="SPFMV CP").exists()

    def test_the_forward_choice_does_not_offer_reverse_primers(self, client, manager):
        reverse_primer = PrimerFactory(direction=PrimerDirection.REVERSE)
        page = client.get(reverse("pcr:assay-new")).context["form"]
        assert reverse_primer not in page.fields["forward_primer"].queryset
        assert reverse_primer in page.fields["reverse_primer"].queryset

    @pytest.mark.parametrize("name", ["pcr:primer-list", "pcr:program-list", "pcr:assay-list"])
    def test_only_a_manager_sees_the_buttons(self, client, name, editor):
        new = name.replace("-list", "-new")
        assert reverse(new) not in client.get(reverse(name)).content.decode()
        client.force_login(UserFactory(role=GlobalRole.PLATFORM_MANAGER))
        assert reverse(new) in client.get(reverse(name)).content.decode()


class TestPublicRecords:
    def test_an_editor_records_a_genbank_sequence_on_a_sample(self, client, editor, project):
        sample = SampleFactory(project=project)
        response = client.post(
            reverse("samples:sample-sequence", args=[sample.pk]),
            {
                "accession": "MK123456",
                "version": 1,
                "organism": "Sweet potato feathery mottle virus",
                "length_bp": 10600,
                "topology": "linear",
            },
        )
        assert response.status_code == 302
        record = SequenceRecord.objects.get(accession="MK123456")
        assert record.sample == sample
        assert "MK123456" in client.get(response.url).content.decode()

    def test_the_sample_page_offers_the_button_to_editors_only(self, client, editor, project):
        sample = SampleFactory(project=project)
        link = reverse("samples:sample-sequence", args=[sample.pk])
        assert (
            link in client.get(reverse("samples:sample-detail", args=[sample.pk])).content.decode()
        )
        client.force_login(UserFactory(role=GlobalRole.GUEST))
        assert client.get(link).status_code in (403, 404)

    def test_an_editor_records_an_existing_biosample(self, client, editor, project):
        sample = SampleFactory(project=project)
        response = client.post(
            reverse("deposits:accession-new"),
            {
                "project": project.pk,
                "kind": "biosample",
                "accession": "samn12345678",
                "sample": sample.pk,
            },
        )
        assert response.status_code == 302
        assert PublicAccession.objects.get(accession="SAMN12345678").sample == sample

    def test_a_bioproject_needs_no_sample(self, client, editor, project):
        response = client.post(
            reverse("deposits:accession-new"),
            {"project": project.pk, "kind": "bioproject", "accession": "PRJNA123456"},
        )
        assert response.status_code == 302

    def test_a_malformed_accession_is_refused(self, client, editor, project):
        response = client.post(
            reverse("deposits:accession-new"),
            {"project": project.pk, "kind": "bioproject", "accession": "not-an-accession"},
        )
        assert response.status_code == 200
        assert not PublicAccession.objects.exists()

    def test_only_editable_projects_are_offered(self, client, editor):
        other = ProjectFactory(code="other")
        response = client.post(
            reverse("deposits:accession-new"),
            {"project": other.pk, "kind": "bioproject", "accession": "PRJNA123456"},
        )
        assert response.status_code == 200
        assert not PublicAccession.objects.exists()
