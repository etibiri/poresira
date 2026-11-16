# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from poresira.core.models import ProjectRole
from poresira.core.tests.factories import ProjectFactory, ProjectMembershipFactory
from poresira.deposits import services
from poresira.deposits.collect import (
    preflight,
    reads_spec,
    root_sample,
    sample_spec,
    study_spec,
)
from poresira.deposits.models import Deposit, Environment, ItemKind
from poresira.deposits.tests.test_deposit_data import reads_file, sequenced
from poresira.storage.models import BackendKind, Integrity
from poresira.storage.tests.factories import StorageBackendFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return ProjectFactory()


@pytest.fixture
def editor(project):
    return ProjectMembershipFactory(project=project, role=ProjectRole.CONTRIBUTOR).user


@pytest.fixture
def ready(editor, project):
    """A deposit of two samples whose reads files are in place."""
    return deposit_of(editor, project)


def deposit_of(editor, project, files=True, **sequencing):
    run, _members = sequenced(project, barcodes=(1, 2), **sequencing)
    if files:
        for number in (1, 2):
            reads_file(project, run, f"pass/barcode0{number}/a.fastq.gz")
    return services.prepare_deposit(user=editor, run=run, title="Sweetpotato viromes")


def messages(findings):
    return [finding.message for finding in findings]


class TestSpecs:
    def test_a_sample_describes_where_when_and_what_without_naming_the_collector(self, ready):
        item = ready.items.filter(kind=ItemKind.SAMPLE).first()
        event = item.sample.collection_event
        event.collector = "A Farmer"
        event.save()

        spec = sample_spec(item)

        values = {a.tag: a.value for a in spec.attributes}
        assert values["collection date"] == "2016-04-16"
        assert values["geographic location (country and/or sea)"] == "Burkina Faso"
        assert values["geographic location (region and locality)"] == "Centre-Sud, Nahouri, Tiebele"
        assert values["geographic location (latitude)"] == "11.08"
        assert values["geographic location (longitude)"] == "-0.9"
        assert values["host scientific name"] == "Ipomoea batatas"
        assert values["symptom severity (scale 1 to 9)"] == "7"
        assert (spec.tax_id, spec.scientific_name) == (4120, "Ipomoea batatas")
        assert "A Farmer" not in repr(spec)
        assert spec.alias == item.alias

    def test_the_study_takes_the_title_and_the_alias_of_its_item(self, ready):
        spec = study_spec(ready)

        assert spec.title == "Sweetpotato viromes" and spec.alias == f"{ready.identifier}_study"

    def test_the_reads_use_the_deposit_settings_and_the_files_where_the_agent_finds_them(
        self, ready
    ):
        item = ready.items.filter(kind=ItemKind.READS).first()

        spec = reads_spec(item, study="PRJEB1", sample="SAMEA1")

        assert (spec.instrument, spec.library_strategy) == ("MinION", "WGS")
        assert spec.name == item.alias and spec.study == "PRJEB1" and spec.sample == "SAMEA1"
        (file,) = spec.files
        row = item.files.get()
        assert file.path == f"{row.file.backend.root}/{row.file.path}" and file.kind == "fastq"

    def test_the_field_sample_is_the_root_of_the_lineage(self, ready):
        product = ready.items.filter(kind=ItemKind.READS).first().sample

        assert root_sample(product).parent_id is None
        assert root_sample(product) == product.parent


class TestPreflight:
    def test_a_complete_deposit_is_ready_and_a_report_has_no_findings(self, ready):
        report = preflight(ready)

        assert report.ready and not report.errors and not report.warnings

    def test_a_deposit_without_samples_or_reads_is_refused(self, project):
        from poresira.deposits.tests.factories import DepositFactory

        report = preflight(DepositFactory(project=project))

        assert any("at least one sample" in m for m in messages(report.errors))

    def test_a_sample_lacks_what_the_archive_requires(self, editor, project):
        deposit = deposit_of(
            editor,
            project,
            sample_attributes={"host_tax_id": None},
            event_attributes={"collected_on": None, "country": ""},
        )

        errors = messages(preflight(deposit).errors)

        assert "Tie_BFA1 lacks the collection date." in errors
        assert "Tie_BFA1 lacks the country." in errors
        assert "Tie_BFA1 lacks the host species and its NCBI taxon ID." in errors

    def test_precise_coordinates_are_a_warning_not_an_error(self, editor, project):
        deposit = deposit_of(
            editor,
            project,
            event_attributes={"latitude": Decimal("11.08235"), "longitude": Decimal("-0.90123")},
        )

        report = preflight(deposit)

        assert report.ready
        assert any("5 decimals" in m for m in messages(report.warnings))

    def test_coordinates_at_the_public_precision_raise_no_warning(self, ready):
        assert not preflight(ready).warnings

    def test_a_changed_or_offline_file_stops_the_deposit(self, editor, project):
        offline = StorageBackendFactory(kind=BackendKind.OFFLINE, root="Tape")
        deposit = deposit_of(editor, project, files=False)
        run = deposit.items.filter(kind=ItemKind.READS).first().sequencing_run
        damaged = reads_file(project, run, "pass/barcode01/a.fastq.gz")
        reads_file(project, run, "pass/barcode02/a.fastq.gz", backend=offline)
        deposit = services.prepare_deposit(user=editor, run=run, title="T")
        damaged.integrity = Integrity.MISMATCH
        damaged.save()

        errors = messages(preflight(deposit).errors)

        assert "a.fastq.gz has changed or is missing." in errors
        assert "a.fastq.gz is on an offline medium." in errors

    def test_reads_without_a_file_are_named(self, editor, project):
        deposit = deposit_of(editor, project, files=False)

        errors = messages(preflight(deposit).errors)

        assert "Tie_BFA1 has no reads file." in errors

    def test_the_run_must_be_completed(self, editor, project):
        deposit = deposit_of(editor, project, completed=False)

        assert any("is not completed" in m for m in messages(preflight(deposit).errors))

    def test_the_instrument_must_be_known_to_the_archive(self, editor, project):
        deposit = deposit_of(editor, project, instrument=None)

        assert any("not known to the archive" in m for m in messages(preflight(deposit).errors))

    def test_a_reconstructed_run_is_a_warning(self, editor, project):
        deposit = deposit_of(editor, project, reconstructed=True)

        report = preflight(deposit)

        assert report.ready
        assert any("reconstructed" in m for m in messages(report.warnings))

    def test_a_production_deposit_needs_a_release_date_in_the_future(self, ready):
        Deposit.objects.filter(pk=ready.pk).update(
            environment=Environment.PRODUCTION,
            hold_until=date.today() - timedelta(days=1),
            compliance_confirmed_at=timezone.now(),
        )
        ready.refresh_from_db()

        assert any("release date" in m for m in messages(preflight(ready).errors))
        past = preflight(ready, today=date(2020, 1, 1))
        assert not any("release date" in m for m in messages(past.errors))
