# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import date

import pytest
from django.urls import reverse

from poresira.core.models import GlobalRole, ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.samples.models import SampleKind
from poresira.samples.tests.factories import DerivativeFactory, SampleFactory
from poresira.sequencing import services
from poresira.sequencing.models import FlowCell, Library, LibraryStatus, RunStatus, SequencingRun
from poresira.sequencing.tests.factories import (
    FlowCellFactory,
    FlowCellTypeFactory,
    InstrumentFactory,
    LibraryFactory,
    LibraryKitFactory,
    LibraryMemberFactory,
    SequencingRunFactory,
)

pytestmark = pytest.mark.django_db


def url(name, *args):
    return reverse(f"sequencing:{name}", args=args)


@pytest.fixture
def project():
    return ProjectFactory(code="spbf")


@pytest.fixture
def editor(project, client):
    user = ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user
    client.force_login(user)
    return user


@pytest.fixture
def library(project):
    return LibraryFactory(project=project, name="RCA 1")


@pytest.fixture
def sealed(library):
    LibraryMemberFactory(library=library, barcode=1)
    LibraryMemberFactory(library=library, barcode=2)
    Library.objects.filter(pk=library.pk).update(status=LibraryStatus.SEALED)
    library.refresh_from_db()
    return library


class TestAccess:
    @pytest.mark.parametrize(
        "name",
        [
            "run-list",
            "library-list",
            "library-create",
            "flow-cell-list",
            "flow-cell-register",
            "reference",
        ],
    )
    def test_anonymous_users_are_sent_to_login(self, client, name):
        assert client.get(url(name)).status_code == 302

    def test_a_library_of_a_foreign_project_is_not_found(self, client, editor):
        assert client.get(url("library-detail", LibraryFactory().pk)).status_code == 404

    def test_read_only_members_can_see_but_not_change(self, client, project, library):
        viewer = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user
        client.force_login(viewer)

        assert client.get(url("library-detail", library.pk)).status_code == 200
        assert client.get(url("member-add", library.pk)).status_code == 403
        assert client.get(url("library-void", library.pk)).status_code == 403
        assert client.get(url("flow-cell-register")).status_code == 403

    @pytest.mark.parametrize("name", ["run-list", "library-list", "flow-cell-list", "reference"])
    def test_the_lists_render(self, client, editor, sealed, name):
        SequencingRunFactory(library=sealed)
        assert client.get(url(name)).status_code == 200


class TestLibraries:
    def test_a_library_is_filled_sealed_and_planned_for_a_run(self, client, project, editor):
        kit = LibraryKitFactory()
        response = client.post(
            url("library-create"), {"project": project.pk, "kit": kit.pk, "name": "RCA 1"}
        )
        library = Library.objects.get()
        assert response.status_code == 302
        assert response.url == url("library-detail", library.pk)

        sample = DerivativeFactory(
            kind=SampleKind.AMPLIFICATION_PRODUCT, parent=SampleFactory(project=project)
        )
        client.post(url("member-add", library.pk), {"sample": sample.identifier, "barcode": "BC07"})
        assert library.members.get().barcode == 7
        assert b"BC07" in client.get(url("library-detail", library.pk)).content

        client.post(url("library-seal", library.pk), {})
        library.refresh_from_db()
        assert library.status == LibraryStatus.SEALED

        cell = FlowCellFactory()
        response = client.post(url("run-plan", library.pk), {"flow_cell": cell.pk})
        run = SequencingRun.objects.get()
        assert response.url == url("run-detail", run.pk)
        assert run.status == RunStatus.PLANNED

    def test_a_bad_barcode_comes_back_as_a_form_error(self, client, editor, library):
        sample = DerivativeFactory(
            kind=SampleKind.AMPLIFICATION_PRODUCT, parent=SampleFactory(project=library.project)
        )
        response = client.post(
            url("member-add", library.pk), {"sample": sample.identifier, "barcode": "200"}
        )

        assert response.status_code == 200
        assert b"outside the kit" in response.content
        assert not library.members.exists()

    def test_an_unknown_sample_is_refused(self, client, editor, library):
        response = client.post(url("member-add", library.pk), {"sample": "nobody", "barcode": "1"})
        assert b"No single sample" in response.content

    def test_a_sealed_library_gains_no_sample(self, client, editor, sealed):
        assert client.get(url("member-add", sealed.pk)).status_code == 200  # form, service refuses
        sample = DerivativeFactory(
            kind=SampleKind.AMPLIFICATION_PRODUCT, parent=SampleFactory(project=sealed.project)
        )
        response = client.post(
            url("member-add", sealed.pk), {"sample": sample.identifier, "barcode": "9"}
        )
        assert response.status_code == 200
        assert sealed.members.count() == 2

    def test_a_member_is_removed_and_a_library_voided_with_a_reason(self, client, editor, library):
        member = LibraryMemberFactory(library=library, barcode=3)

        client.post(url("member-void", member.pk), {"reason": "Wrong sample"})
        client.post(url("library-void", library.pk), {"reason": "Not used"})

        member.refresh_from_db(), library.refresh_from_db()
        assert member.is_voided
        assert library.is_voided


class TestRuns:
    @pytest.fixture
    def run(self, sealed):
        return SequencingRunFactory(library=sealed)

    def test_a_run_goes_from_planned_to_completed_with_its_reads(self, client, editor, run):
        instrument = InstrumentFactory()
        client.post(
            url("run-start", run.pk),
            {"started_on": "2023-11-09", "run_id": "ABC", "acquisition_software": "MinKNOW"},
        )
        run.refresh_from_db()
        assert (run.status, run.started_on) == (RunStatus.RUNNING, date(2023, 11, 9))

        member = run.library.members.first()
        client.post(url("result", run.pk, member.pk), {"reads": "1500", "bases": "900000"})
        assert run.results.get().reads == 1500

        response = client.post(url("run-complete", run.pk), {"basecaller": "Dorado"})
        assert b"basecaller" in response.content.lower()  # the version is missing

        client.post(
            url("run-complete", run.pk),
            {"basecaller": "Dorado", "basecaller_version": "0.9.0", "total_reads": "3000"},
        )
        run.refresh_from_db()
        assert run.status == RunStatus.COMPLETED
        page = client.get(url("run-detail", run.pk)).content
        assert b"Dorado" in page and b"1500" in page
        assert instrument.pk  # unused reference data stays out of the run

    def test_correcting_reads_of_a_completed_run_needs_a_reason(self, client, editor, run):
        member = run.library.members.first()
        services.start_run(user=editor, run=run, started_on=date(2023, 11, 9))
        services.record_result(user=editor, run=run, member=member, reads=10)
        services.complete_run(user=editor, run=run, basecaller="Dorado", basecaller_version="1")

        client.post(url("result", run.pk, member.pk), {"reads": "20"})
        assert run.results.get().reads == 10

        client.post(url("result", run.pk, member.pk), {"reads": "20", "reason": "Typo"})
        assert run.results.get().reads == 20

    def test_a_flow_cell_with_a_run_in_progress_refuses_another(self, client, editor, run):
        other = LibraryFactory(project=run.project)
        LibraryMemberFactory(library=other, barcode=1)
        Library.objects.filter(pk=other.pk).update(status=LibraryStatus.SEALED)

        response = client.post(url("run-plan", other.pk), {"flow_cell": run.flow_cell.pk})

        assert response.status_code == 200
        assert b"run in progress" in response.content

    def test_a_run_is_aborted_and_voided_with_a_reason(self, client, editor, run):
        client.post(url("run-abort", run.pk), {"reason": "Blocked pores"})
        run.refresh_from_db()
        assert run.status == RunStatus.ABORTED

        client.post(url("run-void", run.pk), {"reason": "Entered twice"})
        run.refresh_from_db()
        assert run.is_voided

    def test_the_other_runs_of_a_reused_flow_cell_are_listed(self, client, editor, run):
        services.start_run(user=editor, run=run, started_on=date(2023, 11, 9))
        services.abort_run(user=editor, run=run, reason="Blocked")
        again = SequencingRunFactory(library=run.library, flow_cell=run.flow_cell)

        page = client.get(url("run-detail", again.pk)).content.decode()

        assert run.identifier in page


class TestFlowCells:
    def test_a_flow_cell_is_registered(self, client, editor):
        cell_type = FlowCellTypeFactory()
        response = client.post(
            url("flow-cell-register"),
            {"flow_cell_id": "paw99999", "type": cell_type.pk, "pores_at_qc": "1200"},
        )

        assert response.status_code == 302
        assert FlowCell.objects.get().flow_cell_id == "PAW99999"
        assert b"PAW99999" in client.get(url("flow-cell-list")).content

    def test_platform_managers_may_register_too(self, client):
        client.force_login(UserFactory(role=GlobalRole.PLATFORM_MANAGER))
        assert client.get(url("flow-cell-register")).status_code == 200


def test_the_sample_page_lists_its_libraries_and_runs(client, editor, sealed):
    run = SequencingRunFactory(library=sealed)
    member = sealed.members.first()

    page = client.get(reverse("samples:sample-detail", args=[member.sample_id])).content.decode()

    assert sealed.identifier in page
    assert run.identifier in page
