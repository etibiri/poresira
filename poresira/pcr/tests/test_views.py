# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import date

import pytest
from django.urls import reverse

from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.pcr import services
from poresira.pcr.models import PcrReaction, PcrRun, Result, Role, RunStatus
from poresira.pcr.tests.factories import AssayFactory, PcrRunFactory
from poresira.samples.tests.factories import SampleFactory

pytestmark = pytest.mark.django_db


def url(name, *args):
    return reverse(f"pcr:{name}", args=args)


@pytest.fixture
def project():
    return ProjectFactory(code="spbf")


@pytest.fixture
def editor(project, client):
    user = ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user
    client.force_login(user)
    return user


@pytest.fixture
def run(project):
    return PcrRunFactory(project=project, assay=AssayFactory(name="SPFMV CP"))


@pytest.fixture
def sample(project):
    return SampleFactory(project=project, label="Tie_BFA266")


class TestAccess:
    @pytest.mark.parametrize(
        "name", ["run-list", "run-create", "assay-list", "primer-list", "program-list"]
    )
    def test_anonymous_users_are_sent_to_login(self, client, name):
        assert client.get(url(name)).status_code == 302

    @pytest.mark.parametrize(
        "name", ["run-detail", "reaction-add", "run-perform", "run-close", "run-reopen", "run-void"]
    )
    def test_anonymous_users_cannot_reach_a_run(self, client, run, name):
        assert client.get(url(name, run.pk)).status_code == 302

    def test_a_run_of_a_foreign_project_is_not_found(self, client, editor):
        foreign = PcrRunFactory()
        assert client.get(url("run-detail", foreign.pk)).status_code == 404

    def test_read_only_members_can_see_but_not_change(self, client, project, run):
        viewer = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user
        client.force_login(viewer)
        assert client.get(url("run-detail", run.pk)).status_code == 200
        assert client.get(url("reaction-add", run.pk)).status_code == 403
        assert client.post(url("run-void", run.pk), {"reason": "x"}).status_code == 403

    def test_a_voided_run_cannot_be_changed(self, client, editor, run):
        services.void_record(user=editor, record=run, reason="mistake")
        assert client.get(url("reaction-add", run.pk)).status_code == 403


class TestLists:
    def test_runs_are_listed_and_filtered(self, client, editor, run):
        PcrRunFactory(project=run.project, status=RunStatus.CLOSED)
        page = client.get(url("run-list"), {"status": "planned"})
        assert [r.pk for r in page.context["runs"]] == [run.pk]
        assert run.identifier in page.content.decode()

    def test_foreign_runs_are_hidden(self, client, editor, run):
        PcrRunFactory()
        assert [r.pk for r in client.get(url("run-list")).context["runs"]] == [run.pk]

    def test_reference_lists_show_their_records(self, client, editor, run):
        assert "SPFMV CP" in client.get(url("assay-list")).content.decode()
        assert run.assay.forward_primer.sequence in client.get(url("primer-list")).content.decode()
        assert run.assay.cycling_program.name in client.get(url("program-list")).content.decode()


class TestWorkflow:
    def test_plan_a_run_in_tubes(self, client, editor, project):
        assay = AssayFactory()
        response = client.post(
            url("run-create"),
            {
                "project": project.pk,
                "assay": assay.pk,
                "container": "tubes",
                "tube_count": 4,
                "notes": "",
            },
        )
        run = PcrRun.objects.get()
        assert response.status_code == 302 and response["Location"] == url("run-detail", run.pk)
        assert run.tube_count == 4

    def test_a_plate_ignores_the_tube_count_and_tubes_need_one(self, client, editor, project):
        assay = AssayFactory()
        data = {"project": project.pk, "assay": assay.pk, "container": "tubes", "tube_count": ""}
        assert "Give the number of tubes" in client.post(url("run-create"), data).content.decode()
        client.post(url("run-create"), {**data, "container": "plate_96", "tube_count": 5})
        assert PcrRun.objects.get().tube_count is None

    def test_the_whole_life_of_a_run(self, client, editor, run, sample):
        client.post(url("reaction-add", run.pk), {"role": "sample", "sample": sample.identifier})
        client.post(url("reaction-add", run.pk), {"role": "negative_control", "position": "b1"})
        assert sorted(run.reactions.values_list("position", flat=True)) == ["A1", "B1"]

        client.post(url("run-perform", run.pk), {"performed_on": "2020-01-02"})
        run.refresh_from_db()
        assert run.status == RunStatus.PERFORMED

        for reaction, result in zip(
            run.reactions.order_by("position"), ["positive", "negative"], strict=True
        ):
            client.post(url("reaction-result", reaction.pk), {"result": result, "ct": ""})
        assert not run.reactions.filter(result=Result.PENDING).exists()

        client.post(url("run-close", run.pk), {})
        run.refresh_from_db()
        assert run.status == RunStatus.CLOSED

    def test_a_closed_run_needs_a_reason_to_correct_a_result(self, client, editor, run, sample):
        reaction = services.add_reaction(user=editor, run=run, sample=sample)
        services.mark_performed(user=editor, run=run, performed_on=date(2020, 1, 2))
        services.record_result(user=editor, reaction=reaction, result=Result.POSITIVE)
        services.close_run(user=editor, run=run)
        page = client.post(url("reaction-result", reaction.pk), {"result": "negative"})
        assert page.status_code == 200
        reaction.refresh_from_db()
        assert reaction.result == Result.POSITIVE
        client.post(url("reaction-result", reaction.pk), {"result": "negative", "reason": "typo"})
        reaction.refresh_from_db()
        assert reaction.result == Result.NEGATIVE

    def test_service_errors_come_back_on_the_form(self, client, editor, run):
        page = client.post(url("run-perform", run.pk), {"performed_on": "2020-01-02"})
        assert "Add at least one reaction" in page.content.decode()

    def test_an_unknown_template_is_refused(self, client, editor, run):
        page = client.post(url("reaction-add", run.pk), {"role": "sample", "sample": "nope"})
        assert "No single sample" in page.content.decode()
        assert not PcrReaction.objects.exists()

    def test_a_reaction_can_be_voided_and_a_run_reopened_with_a_reason(
        self, client, editor, run, sample
    ):
        reaction = services.add_reaction(user=editor, run=run, sample=sample)
        client.post(url("reaction-void", reaction.pk), {"reason": "wrong tube"})
        reaction.refresh_from_db()
        assert reaction.is_voided
        other = services.add_reaction(user=editor, run=run, role=Role.NEGATIVE_CONTROL)
        services.mark_performed(user=editor, run=run, performed_on=date(2020, 1, 2))
        services.record_result(user=editor, reaction=other, result=Result.NEGATIVE)
        services.close_run(user=editor, run=run)
        client.post(url("run-reopen", run.pk), {"reason": "recheck"})
        run.refresh_from_db()
        assert run.status == RunStatus.PERFORMED


class TestRunPage:
    def test_a_plate_shows_96_cells_and_a_tube_run_only_its_tubes(self, client, editor, run):
        page = client.get(url("run-detail", run.pk))
        assert sum(len(row["cells"]) for row in page.context["grid"]["rows"]) == 96
        tubes = PcrRunFactory(project=run.project, container="tubes", tube_count=14)
        grid = client.get(url("run-detail", tubes.pk)).context["grid"]
        assert [len(row["cells"]) for row in grid["rows"]] == [12, 2]

    def test_validity_and_history_are_shown(self, client, editor, run, sample):
        services.add_reaction(user=editor, run=run, sample=sample)
        page = client.get(url("run-detail", run.pk))
        assert "Validity not assessed" in page.content.decode()
        assert any(entry.subject == "Reaction A1" for entry in page.context["entries"])

    def test_the_sample_page_lists_its_pcr_results(self, client, editor, run, sample):
        services.add_reaction(user=editor, run=run, sample=sample)
        page = client.get(reverse("samples:sample-detail", args=[sample.pk]))
        assert run.identifier in page.content.decode()
