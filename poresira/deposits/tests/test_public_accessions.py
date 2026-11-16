# SPDX-License-Identifier: AGPL-3.0-or-later
from io import StringIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, IntegrityError, transaction

from poresira.core.audit import verify_audit_chain
from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.deposits import services
from poresira.deposits.importing import AccessionImportError, import_public_accessions
from poresira.deposits.known import family, known_bioproject, known_for
from poresira.deposits.models import AccessionKind, PublicAccession
from poresira.deposits.tests.factories import PublicAccessionFactory
from poresira.samples.models import SampleKind, SequenceRecord
from poresira.samples.tests.factories import DerivativeFactory, SampleFactory

pytestmark = pytest.mark.django_db


def refuses(error, action):
    with pytest.raises(error), transaction.atomic():
        action()


@pytest.fixture
def project():
    return ProjectFactory(code="SPBF")


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


def sequence(sample, accession="PV947756", biosample=""):
    return SequenceRecord.objects.create(
        sample=sample,
        accession=accession,
        organism="Sweet potato leaf curl virus",
        length_bp=2829,
        topology="circular",
        biosample_accession=biosample,
    )


class TestModel:
    @pytest.mark.parametrize(
        ("kind", "accession", "valid"),
        [
            ("biosample", "SAMN57660728", True),
            ("biosample", "SAMEA1234567", True),
            ("biosample", "SRR38354347", False),
            ("sra_run", "SRR38354347", True),
            ("sra_run", "ERR1234567", True),
            ("sra_run", "SRX33188775", False),
            ("sra_experiment", "SRX33188775", True),
            ("sra_experiment", "SRR1", False),
            ("bioproject", "PRJNA1460897", True),
            ("bioproject", "PRJEB12345", True),
            ("bioproject", "SAMN1", False),
        ],
    )
    def test_accessions_follow_the_archive_patterns(self, project, kind, accession, valid):
        def make():
            PublicAccessionFactory(project=project, kind=kind, accession=accession)

        if valid:
            make()
        else:
            refuses(IntegrityError, make)

    def test_only_a_bioproject_has_no_sample(self, project):
        refuses(
            IntegrityError,
            lambda: PublicAccessionFactory(
                project=project, kind="biosample", accession="SAMN1", sample=None
            ),
        )
        refuses(
            IntegrityError,
            lambda: PublicAccessionFactory(
                project=project,
                kind="bioproject",
                accession="PRJNA1",
                sample=SampleFactory(project=project),
            ),
        )

    def test_the_sample_belongs_to_the_project(self, project):
        refuses(
            DatabaseError,
            lambda: PublicAccessionFactory(project=project, sample=SampleFactory()),
        )

    def test_an_accession_is_recorded_once_and_free_again_once_voided(self, project, editor):
        first = PublicAccessionFactory(project=project, accession="SAMN1")

        refuses(IntegrityError, lambda: PublicAccessionFactory(project=project, accession="SAMN1"))
        first.void(user=editor, reason="Wrong sample")
        PublicAccessionFactory(project=project, accession="SAMN1")

    def test_what_it_says_cannot_change(self, project):
        record = PublicAccessionFactory(project=project)

        record.accession = "SAMN2"
        refuses(DatabaseError, record.save)
        record.refresh_from_db()
        record.sample = SampleFactory(project=project)
        refuses(DatabaseError, record.save)

    @pytest.mark.parametrize(
        ("kind", "accession", "archive"),
        [
            ("biosample", "SAMN57660728", "NCBI"),
            ("biosample", "SAMEA123", "ENA"),
            ("biosample", "SAMD123", "DDBJ"),
            ("bioproject", "PRJNA1", "NCBI"),
            ("bioproject", "PRJEB1", "ENA"),
            ("sra_run", "SRR1", "NCBI"),
            ("sra_run", "ERR1", "ENA"),
            ("sra_run", "DRR1", "DDBJ"),
            ("sra_experiment", "SRX1", "NCBI"),
        ],
    )
    def test_the_prefix_tells_which_archive_gave_it(self, project, kind, accession, archive):
        record = PublicAccession(kind=kind, accession=accession)

        assert record.archive_name == archive

    def test_changes_are_audited_and_the_chain_is_intact(self, project):
        record = PublicAccessionFactory(project=project)

        assert verify_audit_chain().is_intact
        assert record.events.filter(pgh_label="insert").count() == 1


class TestService:
    def test_an_accession_is_recorded_for_a_field_sample(self, project, editor):
        sample = SampleFactory(project=project)

        record = services.record_public_accession(
            user=editor, project=project, kind="sra_run", accession=" srr38354347 ", sample=sample
        )

        assert record.accession == "SRR38354347"

    def test_a_derivative_a_foreign_sample_and_unknown_kinds_are_refused(self, project, editor):
        parent = SampleFactory(project=project)
        product = DerivativeFactory(kind=SampleKind.AMPLIFICATION_PRODUCT, parent=parent, label="p")
        arguments = {"user": editor, "project": project, "accession": "SAMN1"}

        with pytest.raises(ValidationError, match="field sample or living plant"):
            services.record_public_accession(kind="biosample", sample=product, **arguments)
        with pytest.raises(ValidationError, match="project"):
            services.record_public_accession(kind="biosample", sample=SampleFactory(), **arguments)
        with pytest.raises(ValidationError, match="Unknown kind"):
            services.record_public_accession(kind="gene", sample=parent, **arguments)

    def test_readers_cannot_record(self, project):
        viewer = ProjectMembershipFactory(project=project, role=ProjectRole.VIEWER).user

        with pytest.raises(PermissionDenied):
            services.record_public_accession(
                user=viewer, project=project, kind="bioproject", accession="PRJNA1"
            )


class TestKnown:
    def test_everything_public_about_a_field_sample_is_gathered(self, project):
        field_sample = SampleFactory(project=project)
        product = DerivativeFactory(
            kind=SampleKind.AMPLIFICATION_PRODUCT, parent=field_sample, label="p"
        )
        PublicAccessionFactory(
            project=project, kind="biosample", accession="SAMN7", sample=field_sample
        )
        PublicAccessionFactory(
            project=project, kind="sra_run", accession="SRR9", sample=field_sample
        )
        PublicAccessionFactory(
            project=project, kind="sra_experiment", accession="SRX8", sample=field_sample
        )
        sequence(product, "PV1")
        sequence(field_sample, "PV2")

        known = known_for(field_sample)

        assert known.biosample == "SAMN7"
        assert known.sra_runs == ("SRR9",) and known.sra_experiments == ("SRX8",)
        assert known.genbank == ("PV1", "PV2") and known.archived_reads

    def test_the_biosample_is_also_read_from_the_sequences_made_from_the_sample(self, project):
        field_sample = SampleFactory(project=project)
        product = DerivativeFactory(
            kind=SampleKind.AMPLIFICATION_PRODUCT, parent=field_sample, label="p"
        )
        sequence(product, "PV1", biosample="SAMN57660728")

        known = known_for(field_sample)

        assert known.biosample == "SAMN57660728" and not known.archived_reads

    def test_a_voided_accession_is_forgotten(self, project, editor):
        field_sample = SampleFactory(project=project)
        record = PublicAccessionFactory(project=project, sample=field_sample)
        record.void(user=editor, reason="Wrong")

        assert known_for(field_sample).biosample == ""

    def test_a_sample_nothing_is_known_about_is_empty(self, project):
        known = known_for(SampleFactory(project=project))

        assert (known.biosample, known.genbank, known.archived_reads) == ("", (), False)

    def test_the_family_holds_every_generation(self, project):
        root = SampleFactory(project=project)
        child = DerivativeFactory(kind=SampleKind.DNA_EXTRACT, parent=root, label="e")
        grandchild = DerivativeFactory(
            kind=SampleKind.AMPLIFICATION_PRODUCT, parent=child, label="p"
        )

        assert set(family(root)) == {root, child, grandchild}

    def test_the_bioproject_of_a_project(self, project):
        assert known_bioproject(project) == ""
        PublicAccessionFactory(project=project, kind="bioproject", accession="PRJNA1460897")

        assert known_bioproject(project) == "PRJNA1460897"


class TestImport:
    def rows(self, project):
        sample = SampleFactory(project=project, label="Tie_BFA316")
        return sample, [
            {"sample": "", "kind": "bioproject", "accession": "PRJNA1460897"},
            {"sample": "Tie_BFA316", "kind": "biosample", "accession": "SAMN57660728"},
            {
                "sample": sample.identifier,
                "kind": "sra_run",
                "accession": "SRR38354347",
                "released_on": "2026-05-01",
            },
        ]

    def test_the_rows_are_recorded(self, project, editor):
        sample, rows = self.rows(project)

        report = import_public_accessions(user=editor, project=project, rows=rows)

        assert len(report.created) == 3
        assert known_for(sample).sra_runs == ("SRR38354347",)
        assert str(PublicAccession.objects.get(accession="SRR38354347").released_on) == "2026-05-01"

    def test_one_bad_row_saves_nothing(self, project, editor):
        _sample, rows = self.rows(project)
        rows.append({"sample": "nobody", "kind": "biosample", "accession": "SAMN1"})
        rows.append({"sample": "Tie_BFA316", "kind": "sra_run", "accession": "bad"})

        with pytest.raises(AccessionImportError) as error:
            import_public_accessions(user=editor, project=project, rows=rows)

        assert [p[:6] for p in error.value.problems] == ["line 5", "line 6"]
        assert not PublicAccession.objects.exists()

    def test_unknown_columns_and_empty_files_are_refused(self, project, editor):
        with pytest.raises(AccessionImportError, match="unknown columns"):
            import_public_accessions(user=editor, project=project, rows=[{"run": "x"}])
        with pytest.raises(AccessionImportError, match="no rows"):
            import_public_accessions(user=editor, project=project, rows=[])

    def test_the_command_reads_a_file_and_a_dry_run_saves_nothing(self, project, editor, tmp_path):
        self.rows(project)
        path = tmp_path / "accessions.csv"
        path.write_text(
            "sample,kind,accession,released_on\n,bioproject,PRJNA1460897,\n"
            "Tie_BFA316,sra_run,SRR38354347,2026-05-01\n"
        )
        arguments = [str(path), "--project", "SPBF", "--user", editor.username]
        out = StringIO()

        call_command("import_public_accessions", *arguments, "--dry-run", stdout=out)
        assert "Would record 2" in out.getvalue() and not PublicAccession.objects.exists()

        call_command("import_public_accessions", *arguments, stdout=out)
        assert PublicAccession.objects.count() == 2

    def test_the_command_refuses_a_bad_file(self, project, editor, tmp_path):
        path = tmp_path / "accessions.csv"
        path.write_text("sample,kind,accession\nnobody,biosample,SAMN1\n")

        with pytest.raises(CommandError, match="nothing saved"):
            call_command(
                "import_public_accessions",
                str(path),
                "--project",
                "SPBF",
                "--user",
                editor.username,
                stderr=StringIO(),
            )


def test_the_kinds_have_labels():
    assert {kind.label for kind in AccessionKind} == {
        "BioProject",
        "BioSample",
        "SRA experiment",
        "SRA run",
    }
