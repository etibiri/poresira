# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from poresira.computing import services
from poresira.computing.models import JobInput, JobKind, JobStatus
from poresira.computing.tests.factories import ComputeJobFactory, JobOutputFactory
from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory, UserFactory
from poresira.sequencing.tests.factories import LibraryKitFactory, SequencingRunFactory
from poresira.storage.models import BackendKind, FileKind, Integrity
from poresira.storage.tests.factories import DataFileFactory, StorageBackendFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return ProjectFactory()


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


@pytest.fixture
def run(project):
    kit = LibraryKitFactory(code="SQK-NBD114.96")
    return SequencingRunFactory(library__project=project, library__kit=kit)


def signal(project, run, **attributes):
    fields = {"kind": FileKind.POD5, "path": "p/x.pod5", **attributes}
    return DataFileFactory(project=project, sequencing_run=run, **fields)


class TestPreparing:
    def test_a_job_takes_the_signal_files_of_the_run_by_default(self, editor, project, run):
        first = signal(project, run)
        DataFileFactory(project=project, sequencing_run=run, kind=FileKind.FASTQ, path="r.fastq")
        second = DataFileFactory(
            project=project, sequencing_run=run, kind=FileKind.POD5, path="p/y.pod5"
        )

        job = services.prepare_job(user=editor, run=run, kind=JobKind.BASECALLING)

        assert job.status == JobStatus.DRAFT and job.project == project
        assert job.parameters == {"model": "sup"}
        assert {row.file for row in job.inputs.all()} == {first, second}
        assert all(row.sha256 == row.file.sha256 for row in job.inputs.all())
        assert job.tool == "dorado"

    def test_the_tool_version_comes_from_the_configuration(self, editor, project, run, settings):
        settings.COMPUTING = {**settings.COMPUTING, "DORADO_VERSION": "0.9.0"}
        signal(project, run)

        job = services.prepare_job(user=editor, run=run, kind=JobKind.BASECALLING)

        assert job.tool_version == "0.9.0"

    def test_parameters_are_validated(self, editor, project, run):
        signal(project, run)

        with pytest.raises(ValidationError):
            services.prepare_job(
                user=editor, run=run, kind=JobKind.BASECALLING, parameters={"model": "x; y"}
            )
        with pytest.raises(ValidationError):
            services.prepare_job(
                user=editor, run=run, kind=JobKind.BASECALLING, parameters={"speed": "fast"}
            )

    def test_demultiplexing_reads_the_basecalled_reads_and_knows_the_kit(
        self, editor, project, run
    ):
        basecalling = ComputeJobFactory(
            project=project,
            sequencing_run=run,
            status=JobStatus.SUCCEEDED,
            submitted_at=timezone.now(),
            script="#!/bin/bash\n",
            finished_at=timezone.now(),
        )
        calls = DataFileFactory(
            project=project, sequencing_run=run, kind=FileKind.BAM, path="c.bam"
        )
        JobOutputFactory(job=basecalling, file=calls)

        job = services.prepare_job(user=editor, run=run, kind=JobKind.DEMULTIPLEXING)

        assert job.parameters == {"kit_name": "SQK-NBD114.96"}
        assert [row.file for row in job.inputs.all()] == [calls]

    def test_a_job_without_input_files_is_refused(self, editor, run):
        with pytest.raises(ValidationError, match="no input file"):
            services.prepare_job(user=editor, run=run, kind=JobKind.BASECALLING)
        with pytest.raises(ValidationError, match="no input file"):
            services.prepare_job(user=editor, run=run, kind=JobKind.DEMULTIPLEXING)

    @pytest.mark.parametrize(
        ("attributes", "message"),
        [
            ({"integrity": Integrity.MISMATCH}, "changed or is missing"),
            ({"integrity": Integrity.MISSING}, "changed or is missing"),
            ({"kind": FileKind.FASTQ}, "not a file this job reads"),
        ],
    )
    def test_damaged_or_unsuitable_inputs_are_refused(
        self, editor, project, run, attributes, message
    ):
        file = signal(project, run, **attributes)

        with pytest.raises(ValidationError, match=message):
            services.prepare_job(user=editor, run=run, kind=JobKind.BASECALLING, inputs=[file])

    def test_a_file_on_an_offline_medium_or_of_another_project_or_voided_is_refused(
        self, editor, project, run
    ):
        tape = StorageBackendFactory(kind=BackendKind.OFFLINE, root="Tape")
        offline = signal(project, run, backend=tape)
        foreign = DataFileFactory(kind=FileKind.POD5)
        voided = signal(project, run)
        voided.void(user=editor, reason="Wrong")

        for file, message in (
            (offline, "offline"),
            (foreign, "another project"),
            (voided, "voided"),
        ):
            with pytest.raises(ValidationError, match=message):
                services.prepare_job(user=editor, run=run, kind=JobKind.BASECALLING, inputs=[file])

    def test_a_path_that_cannot_be_quoted_in_a_script_is_refused(self, editor, project, run):
        file = signal(project, run, path="p/bad\nname.pod5")

        with pytest.raises(ValidationError, match="cannot be used in a script"):
            services.prepare_job(user=editor, run=run, kind=JobKind.BASECALLING, inputs=[file])

    def test_readers_and_strangers_cannot_prepare_a_job(self, project, run):
        signal(project, run)
        viewer = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user

        for user in (viewer, UserFactory()):
            with pytest.raises(PermissionDenied):
                services.prepare_job(user=user, run=run, kind=JobKind.BASECALLING)

    def test_a_voided_run_is_not_processed(self, editor, project, run):
        signal(project, run)
        run.void(user=editor, reason="Entered twice")

        with pytest.raises(ValidationError, match="voided run"):
            services.prepare_job(user=editor, run=run, kind=JobKind.BASECALLING)

    def test_nothing_is_saved_when_an_input_is_refused(self, editor, project, run):
        good = signal(project, run)
        bad = DataFileFactory(project=project, sequencing_run=run, kind=FileKind.BAM)

        with pytest.raises(ValidationError):
            services.prepare_job(user=editor, run=run, kind=JobKind.BASECALLING, inputs=[good, bad])

        assert not JobInput.objects.exists()


class TestDraftsAndScripts:
    def test_the_parameters_of_a_draft_can_change(self, editor, project, run):
        signal(project, run)
        job = services.prepare_job(user=editor, run=run, kind=JobKind.BASECALLING)

        services.set_parameters(user=editor, job=job, parameters={"model": "hac"})

        job.refresh_from_db()
        assert job.parameters == {"model": "hac"}
        with pytest.raises(ValidationError):
            services.set_parameters(user=editor, job=job, parameters={"model": "$(x)"})

    def test_a_job_that_left_the_draft_keeps_its_parameters(self, editor, project):
        job = ComputeJobFactory(
            project=project,
            status=JobStatus.RUNNING,
            submitted_at=timezone.now(),
            script="#!/bin/bash\n",
        )

        with pytest.raises(services.ComputeServiceError, match="draft"):
            services.set_parameters(user=editor, job=job, parameters={"model": "fast"})

    def test_the_script_uses_the_registered_inputs_and_the_configuration(
        self, editor, project, run, settings
    ):
        settings.COMPUTING = {
            **settings.COMPUTING,
            "OUTPUT_DIR": "/data/results/",
            "DORADO": "apptainer exec dorado.sif dorado",
            "SLURM_PARTITION": "gpu",
            "SETUP": "module load cuda",
        }
        file = signal(project, run)
        job = services.prepare_job(user=editor, run=run, kind=JobKind.BASECALLING)

        script = services.render_job_script(job)

        assert f"#SBATCH --job-name={job.identifier}" in script
        assert f"cd /data/results/{job.identifier}" in script
        assert f"{file.backend.root}/{file.path}" in script
        assert file.sha256 in script
        assert "apptainer exec dorado.sif dorado basecaller sup input" in script
        assert "#SBATCH --partition=gpu" in script and "module load cuda" in script


class TestVoiding:
    def test_a_draft_or_finished_job_is_voided_with_a_reason(self, editor, project):
        draft = ComputeJobFactory(project=project)

        services.void_record(user=editor, job=draft, reason="Wrong model")

        draft.refresh_from_db()
        assert draft.is_voided

    def test_a_job_in_the_queue_must_be_cancelled_first(self, editor, project):
        queued = ComputeJobFactory(
            project=project,
            status=JobStatus.SUBMITTED,
            submitted_at=timezone.now(),
            script="#!/bin/bash\n",
        )

        with pytest.raises(services.ComputeServiceError, match="Cancel"):
            services.void_record(user=editor, job=queued, reason="Because")

    def test_a_job_with_results_cannot_be_voided(self, editor, project):
        job = ComputeJobFactory(
            project=project,
            status=JobStatus.SUCCEEDED,
            submitted_at=timezone.now(),
            script="#!/bin/bash\n",
            finished_at=timezone.now(),
        )
        output = JobOutputFactory(job=job)

        with pytest.raises(services.RecordInUseError):
            services.void_record(user=editor, job=output.job, reason="No")

    def test_readers_cannot_void(self, project):
        viewer = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user

        with pytest.raises(PermissionDenied):
            services.void_record(user=viewer, job=ComputeJobFactory(project=project), reason="x")
