# SPDX-License-Identifier: AGPL-3.0-or-later
from pathlib import Path

import pytest
from django.urls import reverse

from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.pcr.tests.factories import PcrRunFactory
from poresira.samples.tests.factories import SampleFactory
from poresira.sequencing.tests.factories import SequencingRunFactory
from poresira.storage import services
from poresira.storage.models import DataFile, FileKind, Integrity, Tier
from poresira.storage.tests.factories import DataFileFactory, StorageBackendFactory

pytestmark = pytest.mark.django_db

CONTENT = b"ACGT" * 100


def url(name, *args):
    return reverse(f"storage:{name}", args=args)


@pytest.fixture
def project():
    return ProjectFactory(code="spbf")


@pytest.fixture
def editor(project, client):
    user = ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user
    client.force_login(user)
    return user


@pytest.fixture
def disk(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "reads.fastq.gz").write_bytes(CONTENT)
    return StorageBackendFactory(name="Lab disk", root=str(tmp_path))


@pytest.fixture
def run(project):
    return SequencingRunFactory(library__project=project)


@pytest.fixture
def file(project, run):
    return DataFileFactory(project=project, sequencing_run=run, path="a/reads.fastq.gz")


class TestAccess:
    @pytest.mark.parametrize("name", ["file-list", "backend-list"])
    def test_anonymous_users_are_sent_to_login(self, client, name):
        assert client.get(url(name)).status_code == 302

    @pytest.mark.parametrize("name", ["file-detail", "file-check", "file-tier", "file-void"])
    def test_anonymous_users_cannot_reach_a_file(self, client, file, name):
        assert client.get(url(name, file.pk)).status_code == 302

    def test_a_file_of_a_foreign_project_is_not_found(self, client, editor):
        assert client.get(url("file-detail", DataFileFactory().pk)).status_code == 404

    def test_read_only_members_can_see_but_not_change(self, client, project, file):
        client.force_login(ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user)

        assert client.get(url("file-detail", file.pk)).status_code == 200
        for name in ("file-check", "file-tier", "file-void"):
            assert client.get(url(name, file.pk)).status_code == 403
        link = url("file-register", "sequencing-run", file.sequencing_run_id)
        assert client.get(link).status_code == 403

    def test_the_lists_render_and_filter(self, client, editor, file):
        page = client.get(url("file-list"), {"q": "reads", "kind": "fastq"}).content.decode()
        assert file.identifier in page
        assert file.identifier not in client.get(url("file-list"), {"q": "absent"}).content.decode()

    def test_the_backends_are_listed_with_their_file_counts(self, client, editor, disk, file):
        page = client.get(url("backend-list")).content.decode()

        assert "Lab disk" in page


class TestFilePages:
    def test_the_detail_shows_checksums_the_link_and_the_checks(self, client, editor, file):
        page = client.get(url("file-detail", file.pk)).content.decode()

        assert file.sha256 in page and file.sequencing_run.identifier in page

    def test_a_failed_check_is_flagged(self, client, editor, file):
        file.integrity = Integrity.MISMATCH
        file.save()

        assert b"no longer matches" in client.get(url("file-detail", file.pk)).content

    def test_the_tier_changes_with_a_reason(self, client, editor, file):
        client.post(url("file-tier", file.pk), {"tier": "cold"})
        file.refresh_from_db()
        assert file.tier == Tier.HOT

        client.post(url("file-tier", file.pk), {"tier": "cold", "reason": "Archived"})
        file.refresh_from_db()
        assert file.tier == Tier.COLD

    def test_a_file_is_voided_with_a_reason(self, client, editor, file):
        client.post(url("file-void", file.pk), {"reason": "Wrong run"})
        file.refresh_from_db()

        assert file.is_voided

    def test_the_check_runs_the_task(self, client, editor, project, run, disk):
        recorded = services.register_file(
            user=editor, project=project, backend=disk, path="a/reads.fastq.gz", sequencing_run=run
        )
        with Path(disk.root, "a", "reads.fastq.gz").open("ab") as damaged:
            damaged.write(b"!")

        response = client.post(url("file-check", recorded.pk), {})

        assert response.status_code == 302
        recorded.refresh_from_db()
        assert recorded.integrity == Integrity.MISMATCH
        assert recorded.checks.get().checked_by == editor

    def test_an_offline_file_cannot_be_checked(self, client, editor, project, run):
        tape = StorageBackendFactory(kind="offline", root="Tape")
        offline = DataFileFactory(project=project, backend=tape, sequencing_run=run)

        response = client.post(url("file-check", offline.pk), {})

        assert response.status_code == 200 and b"offline" in response.content


class TestRegistering:
    def test_a_file_is_registered_from_a_run_a_pcr_run_and_a_sample(
        self, client, editor, project, disk, run
    ):
        pcr_run, sample = PcrRunFactory(project=project), SampleFactory(project=project)
        for link, record in (("sequencing-run", run), ("pcr-run", pcr_run), ("sample", sample)):
            response = client.post(
                url("file-register", link, record.pk),
                {"backend": disk.pk, "path": "a/reads.fastq.gz"},
            )
            assert response.status_code == 302, link
            DataFile.objects.get(**{link.replace("-", "_"): record}).void(
                user=editor, reason="Next"
            )

    def test_a_bad_path_comes_back_as_a_form_error(self, client, editor, disk, run):
        response = client.post(
            url("file-register", "sequencing-run", run.pk),
            {"backend": disk.pk, "path": "a/absent.pod5"},
        )

        assert response.status_code == 200 and b"No such file" in response.content
        assert not DataFile.objects.exists()

    def test_the_pages_of_the_records_list_their_files(self, client, editor, project, run, file):
        page = client.get(reverse("sequencing:run-detail", args=[run.pk])).content.decode()

        assert file.name in page
        assert url("file-register", "sequencing-run", run.pk) in page
        sample = SampleFactory(project=project)
        page = client.get(reverse("samples:sample-detail", args=[sample.pk])).content.decode()
        assert url("file-register", "sample", sample.pk) in page

    def test_an_offline_file_is_registered_with_its_checksums(self, client, editor, project, run):
        tape = StorageBackendFactory(kind="offline", root="Tape 3")

        client.post(
            url("file-register", "sequencing-run", run.pk),
            {
                "backend": tape.pk,
                "path": "x/signal.pod5",
                "size_bytes": "10",
                "sha256": "a" * 64,
                "md5": "b" * 32,
                "kind": FileKind.POD5,
            },
        )

        assert DataFile.objects.get().integrity == Integrity.UNCHECKED
