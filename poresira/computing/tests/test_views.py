# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.urls import reverse
from django.utils import timezone

from poresira.computing import services
from poresira.computing.models import ComputeJob, JobKind, JobStatus
from poresira.computing.tests.factories import ComputeJobFactory, JobOutputFactory
from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.sequencing.tests.factories import LibraryKitFactory, SequencingRunFactory
from poresira.storage.models import FileKind
from poresira.storage.tests.factories import DataFileFactory

pytestmark = pytest.mark.django_db


def url(name, *args):
    return reverse(f"computing:{name}", args=args)


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
    kit = LibraryKitFactory(code="SQK-NBD114.96")
    return SequencingRunFactory(library__project=project, library__kit=kit)


@pytest.fixture
def signal(project, run):
    return DataFileFactory(
        project=project, sequencing_run=run, kind=FileKind.POD5, path="runs/x.pod5"
    )


@pytest.fixture
def job(editor, run, signal):
    return services.prepare_job(user=editor, run=run, kind=JobKind.BASECALLING)


def submitted_job(project):
    return ComputeJobFactory(
        project=project,
        status=JobStatus.SUBMITTED,
        submitted_at=timezone.now(),
        script="#!/bin/bash\necho submitted\n",
        scheduler_job_id="4821",
    )


class TestAccess:
    def test_anonymous_users_are_sent_to_login(self, client, job):
        client.logout()
        assert client.get(url("job-list")).status_code == 302
        for name in ("job-detail", "job-submit", "job-cancel", "job-void", "job-script"):
            assert client.get(url(name, job.pk)).status_code == 302
        assert client.get(url("job-new", job.sequencing_run_id, "basecalling")).status_code == 302

    def test_a_job_of_a_foreign_project_is_not_found(self, client, editor):
        assert client.get(url("job-detail", ComputeJobFactory().pk)).status_code == 404

    def test_read_only_members_can_see_but_not_change(self, client, project, job):
        client.force_login(ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user)

        assert client.get(url("job-detail", job.pk)).status_code == 200
        assert client.get(url("job-script", job.pk)).status_code == 200
        for name in ("job-submit", "job-cancel", "job-void"):
            assert client.get(url(name, job.pk)).status_code == 403
        assert client.get(url("job-new", job.sequencing_run_id, "basecalling")).status_code == 403

    def test_an_unknown_kind_is_refused(self, client, editor, run):
        assert client.get(url("job-new", run.pk, "assembly")).status_code == 403


class TestPages:
    def test_the_list_and_detail_show_the_job(self, client, editor, job, signal):
        listing = client.get(url("job-list")).content.decode()
        detail = client.get(url("job-detail", job.pk)).content.decode()

        assert job.identifier in listing
        assert job.identifier in detail and signal.path in detail and signal.sha256 in detail
        assert "model" in detail

    def test_the_list_filters(self, client, editor, job):
        other = ComputeJobFactory(project=job.project, kind=JobKind.DEMULTIPLEXING)

        page = client.get(url("job-list"), {"kind": "demultiplexing"}).content.decode()

        assert other.identifier in page and job.identifier not in page

    def test_a_failed_job_shows_its_exit_code_and_log(self, client, editor, project):
        failed = ComputeJobFactory(
            project=project,
            status=JobStatus.FAILED,
            submitted_at=timezone.now(),
            finished_at=timezone.now(),
            script="#!/bin/bash\n",
            exit_code=137,
            log_path="/data/computing/x/slurm-1.log",
        )

        page = client.get(url("job-detail", failed.pk)).content.decode()

        assert "137" in page and "/data/computing/x/slurm-1.log" in page

    def test_results_link_to_their_file_and_back(self, client, editor, project):
        job = ComputeJobFactory(
            project=project,
            status=JobStatus.SUCCEEDED,
            submitted_at=timezone.now(),
            finished_at=timezone.now(),
            script="#!/bin/bash\n",
        )
        output = JobOutputFactory(job=job)

        assert output.file.path in client.get(url("job-detail", job.pk)).content.decode()
        page = client.get(reverse("storage:file-detail", args=[output.file_id])).content.decode()
        assert job.identifier in page

    def test_the_run_page_lists_its_jobs_and_offers_to_start_one(self, client, editor, job, run):
        page = client.get(reverse("sequencing:run-detail", args=[run.pk])).content.decode()

        assert job.identifier in page
        assert url("job-new", run.pk, "basecalling") in page

    def test_the_script_is_downloaded_as_a_shell_file(self, client, editor, job):
        draft = client.get(url("job-script", job.pk))
        assert draft["Content-Disposition"] == f'attachment; filename="{job.identifier}.sh"'
        assert b"#SBATCH --job-name=" in draft.content

        sent = submitted_job(job.project)
        assert client.get(url("job-script", sent.pk)).content == b"#!/bin/bash\necho submitted\n"


class TestPreparing:
    def test_a_job_is_prepared_from_the_run_page_form(self, client, editor, run, signal):
        page = client.get(url("job-new", run.pk, "basecalling")).content.decode()
        assert "runs/x.pod5" in page and 'name="model"' in page

        response = client.post(
            url("job-new", run.pk, "basecalling"),
            {"model": "hac", "min_qscore": "10", "inputs": [signal.pk], "notes": "Try hac"},
        )

        job = ComputeJob.objects.get()
        assert response.url == url("job-detail", job.pk)
        assert job.parameters == {"model": "hac", "min_qscore": 10}
        assert job.inputs.get().file == signal and job.notes == "Try hac"

    def test_the_kit_of_the_library_is_proposed_for_demultiplexing(
        self, client, editor, project, run
    ):
        calls = DataFileFactory(
            project=project, sequencing_run=run, kind=FileKind.BAM, path="c.bam"
        )
        basecalling = ComputeJobFactory(
            project=project,
            sequencing_run=run,
            status=JobStatus.SUCCEEDED,
            submitted_at=timezone.now(),
            finished_at=timezone.now(),
            script="#!/bin/bash\n",
        )
        JobOutputFactory(job=basecalling, file=calls)

        page = client.get(url("job-new", run.pk, "demultiplexing")).content.decode()

        assert "SQK-NBD114.96" in page and "c.bam" in page

    def test_a_dangerous_parameter_comes_back_as_a_form_error(self, client, editor, run, signal):
        response = client.post(
            url("job-new", run.pk, "basecalling"),
            {"model": "sup; rm -rf /", "inputs": [signal.pk]},
        )

        assert response.status_code == 200
        assert not ComputeJob.objects.exists()

    def test_a_job_needs_input_files(self, client, editor, run):
        response = client.post(url("job-new", run.pk, "basecalling"), {"model": "sup"})

        assert response.status_code == 200 and not ComputeJob.objects.exists()


class TestActions:
    def test_submitting_hands_the_job_to_the_agent(self, client, editor, job, monkeypatch):
        called = []
        monkeypatch.setattr(
            "poresira.computing.views.submit_job_task.delay", lambda *args: called.append(args)
        )

        response = client.post(url("job-submit", job.pk), {})

        assert response.status_code == 302
        assert called == [(str(job.pk), str(editor.pk))]

    def test_only_a_draft_can_be_submitted(self, client, editor, project):
        assert client.get(url("job-submit", submitted_job(project).pk)).status_code == 403

    def test_a_draft_is_cancelled_in_the_database(self, client, editor, job):
        client.post(url("job-cancel", job.pk), {})

        job.refresh_from_db()
        assert job.status == JobStatus.CANCELLED

    def test_a_queued_job_is_cancelled_by_the_agent(self, client, editor, project, monkeypatch):
        sent = submitted_job(project)
        called = []
        monkeypatch.setattr(
            "poresira.computing.views.cancel_job_task.delay", lambda *args: called.append(args)
        )

        client.post(url("job-cancel", sent.pk), {})

        assert called == [(str(sent.pk), str(editor.pk))]

    def test_a_finished_job_cannot_be_cancelled(self, client, editor, job):
        services.cancel_job(user=editor, job=job)

        assert client.get(url("job-cancel", job.pk)).status_code == 403

    def test_a_job_is_voided_with_a_reason(self, client, editor, job):
        client.post(url("job-void", job.pk), {"reason": "Wrong model"})

        job.refresh_from_db()
        assert job.is_voided and job.void_reason == "Wrong model"

    def test_a_queued_job_cannot_be_voided_from_its_page(self, client, editor, project):
        page = client.get(url("job-detail", submitted_job(project).pk)).content.decode()

        assert "/void/" not in page
