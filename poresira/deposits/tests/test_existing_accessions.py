# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deposits and what the public archives already hold (SRA, GenBank, BioSample)."""

from datetime import date, timedelta

import pytest

from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.deposits import services
from poresira.deposits.collect import preflight
from poresira.deposits.models import DepositStatus, ItemKind, ItemStatus
from poresira.deposits.rocrate import build_ro_crate
from poresira.deposits.tests.factories import PublicAccessionFactory
from poresira.deposits.tests.fake_archive import PASSWORD, USER, FakeArchive
from poresira.deposits.tests.test_deposit_data import reads_file, sequenced
from poresira.samples.models import SequenceRecord

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake(tmp_path):
    archive = FakeArchive(tmp_path)
    yield archive
    archive.stop()


@pytest.fixture(autouse=True)
def ena(settings, fake, tmp_path):
    settings.ENA = {
        "WEBIN_USER": USER,
        "WEBIN_PASSWORD": PASSWORD,
        "TEST_URL": fake.url,
        "PRODUCTION_URL": fake.url,
        "WEBIN_CLI": fake.cli,
        "WORK_DIR": str(tmp_path / "work"),
        "TIMEOUT_SECONDS": 10,
        "CLI_TIMEOUT_SECONDS": 30,
    }


@pytest.fixture
def project():
    return ProjectFactory(code="SPBF")


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


@pytest.fixture
def lead(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.LEAD).user


@pytest.fixture
def run(project):
    run, _members = sequenced(project, barcodes=(1, 2))
    for number in (1, 2):
        reads_file(project, run, f"pass/barcode0{number}/a.fastq.gz")
    return run


def field_sample_of(deposit, number=1):
    item = deposit.items.filter(kind=ItemKind.SAMPLE, sample__label=f"Tie_BFA{number}").get()
    return item.sample


def messages(findings):
    return [finding.message for finding in findings]


class TestReadsAlreadyInSra:
    def test_a_test_deposit_only_warns(self, editor, project, run):
        deposit = services.prepare_deposit(user=editor, run=run, title="T")
        PublicAccessionFactory(
            project=project,
            kind="sra_run",
            accession="SRR38354347",
            sample=field_sample_of(deposit),
        )

        report = preflight(deposit)

        assert report.ready
        assert any("already in SRA (SRR38354347)" in m for m in messages(report.warnings))

    def test_the_real_deposit_is_refused_until_those_reads_are_left_out(
        self, editor, lead, project, run
    ):
        test = services.submit_deposit(
            user=editor, deposit=services.prepare_deposit(user=editor, run=run, title="T")
        )
        PublicAccessionFactory(
            project=project,
            kind="sra_experiment",
            accession="SRX33188775",
            sample=field_sample_of(test),
        )
        production = services.copy_for_production(
            user=lead,
            source=test,
            hold_until=date.today() + timedelta(days=365),
            permits_confirmed=True,
        )

        report = preflight(production)

        assert not report.ready
        assert any("already in SRA (SRX33188775)" in m for m in messages(report.errors))
        with pytest.raises(services.DepositServiceError, match="not ready"):
            services.submit_deposit(user=lead, deposit=production, confirm="SPBF")

    def test_a_sample_whose_reads_are_not_in_sra_is_not_flagged(self, editor, run):
        deposit = services.prepare_deposit(user=editor, run=run, title="T")

        assert not preflight(deposit).warnings


class TestExistingBioSamples:
    def test_production_cites_the_biosample_that_exists_instead_of_registering_one(
        self, editor, lead, project, run, fake
    ):
        test = services.submit_deposit(
            user=editor, deposit=services.prepare_deposit(user=editor, run=run, title="T")
        )
        known = field_sample_of(test, 1)
        PublicAccessionFactory(project=project, accession="SAMN57660728", sample=known)

        production = services.copy_for_production(
            user=lead,
            source=test,
            hold_until=date.today() + timedelta(days=365),
            permits_confirmed=True,
        )

        cited = production.items.get(kind=ItemKind.SAMPLE, sample=known)
        assert (cited.status, cited.accession) == (ItemStatus.ACCEPTED, "SAMN57660728")
        assert "Already registered in" in cited.message
        other = production.items.exclude(sample=known).get(kind=ItemKind.SAMPLE)
        assert other.status == ItemStatus.PENDING

    def test_the_biosample_written_on_a_genbank_record_is_cited_too(
        self, editor, lead, project, run
    ):
        test = services.submit_deposit(
            user=editor, deposit=services.prepare_deposit(user=editor, run=run, title="T")
        )
        product = test.items.filter(kind=ItemKind.READS).get(sample__label="Tie_BFA1").sample
        SequenceRecord.objects.create(
            sample=product,
            accession="PV947756",
            organism="Sweet potato leaf curl virus",
            length_bp=2829,
            topology="circular",
            biosample_accession="SAMN57660728",
        )

        production = services.copy_for_production(
            user=lead,
            source=test,
            hold_until=date.today() + timedelta(days=365),
            permits_confirmed=True,
        )

        cited = production.items.get(kind=ItemKind.SAMPLE, sample=product.parent)
        assert cited.accession == "SAMN57660728" and cited.status == ItemStatus.ACCEPTED

    def test_a_cited_biosample_is_not_registered_again_and_the_reads_use_it(
        self, editor, lead, project, run, fake, tmp_path
    ):
        test = services.submit_deposit(
            user=editor, deposit=services.prepare_deposit(user=editor, run=run, title="T")
        )
        for number, accession in ((1, "SAMN57660728"), (2, "SAMN57660729")):
            PublicAccessionFactory(
                project=project, accession=accession, sample=field_sample_of(test, number)
            )
        production = services.copy_for_production(
            user=lead,
            source=test,
            hold_until=date.today() + timedelta(days=365),
            permits_confirmed=True,
        )
        before = len(fake.requests)

        done = services.submit_deposit(user=lead, deposit=production, confirm="SPBF")

        assert done.status == DepositStatus.ACCEPTED
        body = fake.requests[before][1] if len(fake.requests) > before else ""
        assert 'name="SAMPLE"' not in body  # the study alone is registered
        manifest = (tmp_path / "last_manifest").read_text()
        assert "SAMPLE\tSAMN5766072" in manifest

    def test_a_test_deposit_registers_its_own_temporary_samples(self, editor, project, run):
        PublicAccessionFactory(
            project=project,
            accession="SAMN57660728",
            sample=field_sample_of(
                services.prepare_deposit(user=editor, run=run, title="Probe"), 1
            ),
        )

        deposit = services.prepare_deposit(user=editor, run=run, title="T")

        assert all(
            i.status == ItemStatus.PENDING for i in deposit.items.filter(kind=ItemKind.SAMPLE)
        )


class TestWarnings:
    def test_genbank_sequences_and_an_existing_bioproject_are_mentioned(self, editor, project, run):
        deposit = services.prepare_deposit(user=editor, run=run, title="T")
        product = deposit.items.filter(kind=ItemKind.READS).get(sample__label="Tie_BFA1").sample
        SequenceRecord.objects.create(
            sample=product,
            accession="PV947756",
            organism="Sweet potato leaf curl virus",
            length_bp=2829,
            topology="circular",
        )
        PublicAccessionFactory(project=project, kind="bioproject", accession="PRJNA1460897")

        report = preflight(deposit)

        assert report.ready
        warnings = messages(report.warnings)
        assert any("sequences in GenBank (PV947756)" in m for m in warnings)
        assert any("BioProject PRJNA1460897" in m for m in warnings)


class TestRoCrate:
    def test_genbank_sra_and_the_existing_biosample_are_linked(self, editor, project, run):
        deposit = services.prepare_deposit(user=editor, run=run, title="T")
        known = field_sample_of(deposit, 1)
        product = deposit.items.filter(kind=ItemKind.READS).get(sample__label="Tie_BFA1").sample
        SequenceRecord.objects.create(
            sample=product,
            accession="PV947756",
            organism="Sweet potato leaf curl virus",
            length_bp=2829,
            topology="circular",
        )
        PublicAccessionFactory(project=project, accession="SAMN57660728", sample=known)
        PublicAccessionFactory(
            project=project, kind="sra_run", accession="SRR38354347", sample=known
        )

        graph = {e["@id"]: e for e in build_ro_crate(deposit)["@graph"]}

        biosample = graph[f"#sample-{known.identifier}"]
        assert "https://www.ncbi.nlm.nih.gov/biosample/SAMN57660728" in biosample["sameAs"]
        assert biosample["subjectOf"] == [{"@id": "https://www.ncbi.nlm.nih.gov/nuccore/PV947756"}]
        assert graph["https://www.ncbi.nlm.nih.gov/nuccore/PV947756"]["identifier"] == "PV947756"
        sequencing = next(e for e in graph.values() if e["@id"].startswith("#sequencing-"))
        assert "https://www.ncbi.nlm.nih.gov/sra/SRR38354347" in sequencing["sameAs"]
