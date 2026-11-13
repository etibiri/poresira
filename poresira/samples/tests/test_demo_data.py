# SPDX-License-Identifier: AGPL-3.0-or-later
import csv
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction

from poresira.core.audit import verify_audit_chain
from poresira.core.models import Project, User
from poresira.samples.demo import loader
from poresira.samples.lineage import ancestors, family_tree
from poresira.samples.models import (
    CollectionEvent,
    Sample,
    SampleKind,
    SampleStatus,
    SequenceRecord,
    StorageLocation,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="class")
def loaded_demo(django_db_setup, django_db_blocker):
    """Load the demo data once per class, inside a transaction rolled back afterwards."""
    with django_db_blocker.unblock():
        transaction_block = transaction.atomic()
        transaction_block.__enter__()
        try:
            yield loader.load_demo_data()
        finally:
            transaction.set_rollback(True)
            transaction_block.__exit__(None, None, None)


def kind_count(kind):
    return Sample.objects.filter(kind=kind).count()


class TestSourceFiles:
    def test_the_biosample_file_matches_the_published_bioproject(self):
        rows = loader._read("biosamples_PRJNA1460897.csv")

        assert len(rows) == 66
        assert len({row["biosample_accession"] for row in rows}) == 66
        assert {row["host"] for row in rows} == {"Ipomoea batatas"}
        years = [row["collection_date"][:4] for row in rows]
        assert (years.count("2016"), years.count("2024"), years.count("2025")) == (53, 5, 8)

    def test_table_3_has_its_50_samples(self):
        with (loader.DATA_DIR / "article_table3_grafted_plants.csv").open(
            newline="", encoding="utf-8"
        ) as csv_file:
            rows = list(csv.DictReader(csv_file))

        assert len(rows) == 50
        assert len({row["sample"] for row in rows}) == 50


class TestParsing:
    def test_coordinates_and_places(self):
        assert loader.parse_lat_lon("11.08 N 0.90 W") == ("11.08", "-0.90")
        assert loader.parse_lat_lon("12.06 N 0.21 E") == ("12.06", "0.21")
        assert loader.parse_geo_loc("Burkina Faso: Nahouri, Tiebele") == (
            "Burkina Faso",
            "Nahouri",
            "Tiebele",
        )

    def test_isolate_stems_ignore_replicate_markers_and_accents(self):
        assert loader.split_isolate("Tieb_E1_BFA297") == ("tie", "297")
        assert loader.split_isolate("Tie_BFA297") == ("tie", "297")
        assert loader.table3_stem("BFA1308", {"locality": "Sao"}) == ("sao", "1308")
        assert loader.table3_stem("BFA340", {"locality": "Bagré"}) == ("bag", "340")

    def test_symptom_codes_are_spelled_out_and_unknown_codes_kept(self):
        assert loader.expand_symptoms("Vc, Lc") == "Vein-clearing, leaf curling"
        assert loader.expand_symptoms("A") == "Asymptomatic"
        assert loader.expand_symptoms("Vc, C, P") == "Vein-clearing, chlorosis, P"


class TestLoading:
    def test_the_published_sample_counts_are_reproduced(self, loaded_demo):
        assert not loaded_demo.already_loaded
        assert kind_count(SampleKind.DNA_EXTRACT) == 53
        assert kind_count(SampleKind.AMPLIFICATION_PRODUCT) == 53
        assert kind_count(SampleKind.RNA_EXTRACT) == 13
        assert kind_count(SampleKind.FIELD_SAMPLE) == 109
        assert kind_count(SampleKind.LIVING_PLANT) == 50 + 13

    def test_the_54_published_sequences_are_linked_to_their_sequenced_material(self, loaded_demo):
        records = SequenceRecord.objects.select_related("sample")

        assert records.count() == 54
        assert loaded_demo.counts["sequences"] == 54
        rna = records.filter(organism="Sweet potato feathery mottle virus")
        assert rna.count() == 4
        assert {r.sample.kind for r in rna} == {SampleKind.RNA_EXTRACT}
        dna = records.exclude(pk__in=rna)
        assert dna.count() == 50  # 20 SPLCV, 23 deltasatellites, 7 from the MDPI paper
        assert {r.sample.kind for r in dna} == {SampleKind.AMPLIFICATION_PRODUCT}

    def test_one_sample_can_carry_several_sequences(self, loaded_demo):
        sample = Sample.objects.get(kind=SampleKind.AMPLIFICATION_PRODUCT, label="Kan_BFA662")

        accessions = set(sample.sequence_records.values_list("accession", flat=True))

        assert accessions == {"PV947740", "PV947692", "PV405576"}  # SPLCV, deltasatellite, alpha

    def test_both_studies_are_cited(self, loaded_demo):
        notes = SequenceRecord.objects.get(accession="PV405580").notes
        assert "10.3390/v17091222" in notes
        assert (
            "10.3389/fmicb.2026.1722370" in SequenceRecord.objects.get(accession="PV947692").notes
        )

    def test_a_date_that_disagrees_with_the_biosample_is_flagged(self, loaded_demo):
        record = SequenceRecord.objects.get(accession="PV405577")

        assert record.sample.label == "Ban_BFA1094"
        assert "2016-08-18" in record.notes
        assert "2016-08-16" in record.notes

    def test_a_genome_reaches_its_biosample_through_the_lineage(self, loaded_demo):
        record = SequenceRecord.objects.get(accession="PV947737")

        assert record.biosample_accession == "SAMN57660677"
        root = ancestors(record.sample)[0]
        assert root.kind == SampleKind.FIELD_SAMPLE
        assert root.label == "Kan_BFA160"

    def test_genomes_whose_isolate_name_differs_are_flagged(self, loaded_demo):
        record = SequenceRecord.objects.get(accession="PV947757")

        assert record.sample.label == "Tieb_E1_BFA297"
        assert "isolate name differs" in record.notes
        assert "confirmed by the submitting author" in record.notes
        assert "differs" not in SequenceRecord.objects.get(accession="PV947737").notes

    def test_every_derivative_traces_back_to_a_field_sample(self, loaded_demo):
        for sample in Sample.objects.exclude(kind=SampleKind.FIELD_SAMPLE):
            assert ancestors(sample)[0].kind == SampleKind.FIELD_SAMPLE

    def test_a_published_sample_has_its_published_attributes(self, loaded_demo):
        sample = Sample.objects.get(kind=SampleKind.FIELD_SAMPLE, label="Tie_BFA266")
        event = sample.collection_event

        assert (event.province, event.locality) == ("Nahouri", "Tiebele")
        assert str(event.collected_on) == "2016-04-15"
        assert (str(event.latitude), str(event.longitude)) == ("11.12000", "-0.96000")
        assert (sample.host_taxon, sample.host_tax_id) == ("Ipomoea batatas", 4120)
        rca = Sample.objects.get(kind=SampleKind.AMPLIFICATION_PRODUCT, label="Tie_BFA266")
        assert "SAMN57660678" in rca.notes
        assert [node.sample.kind for node in family_tree(sample)] == [
            SampleKind.FIELD_SAMPLE,
            SampleKind.DNA_EXTRACT,
            SampleKind.AMPLIFICATION_PRODUCT,
        ]

    def test_symptoms_come_from_table_3_where_the_sample_matches(self, loaded_demo):
        matched = Sample.objects.get(kind=SampleKind.FIELD_SAMPLE, label="Bag_BFA340")
        grafted = Sample.objects.get(kind=SampleKind.LIVING_PLANT, label="Bag_BFA340 graft")

        assert matched.symptom_notes == "Asymptomatic"
        assert grafted.host_taxon == "Ipomoea setosa"
        assert grafted.host_tax_id is None
        assert grafted.symptom_notes == "Interveinal chlorosis"

    def test_greenhouse_plants_hang_below_their_field_sample(self, loaded_demo):
        replicate = Sample.objects.get(kind=SampleKind.RNA_EXTRACT, label="Tieb_E1_BFA297")
        chain = ancestors(replicate)

        assert [sample.kind for sample in chain] == [
            SampleKind.FIELD_SAMPLE,
            SampleKind.LIVING_PLANT,
        ]
        assert chain[0].label == "Tie_BFA297"

    def test_unmatched_greenhouse_plants_get_a_flagged_inferred_parent(self, loaded_demo):
        inferred = Sample.objects.get(kind=SampleKind.FIELD_SAMPLE, label="Kot_BFA781")

        assert "inferred" in inferred.notes
        assert inferred.collection_event.collected_on is None

    def test_storage_follows_the_articles_and_every_position_is_distinct(self, loaded_demo):
        assert StorageLocation.objects.get(name="Freezer -80 °C").nominal_temperature_c == -80
        assert Sample.objects.get(
            kind=SampleKind.RNA_EXTRACT, label="Yor_BFA412"
        ).storage_location.path == ("LVBV laboratory, INERA Kamboinsé > Freezer -80 °C > RNA box 1")
        placed = Sample.objects.exclude(position="")
        assert placed.count() == 53 + 53 + 13
        assert len({(s.storage_location_id, s.position) for s in placed}) == placed.count()
        assert Sample.objects.filter(status=SampleStatus.STORED).count() == 109 + 53 + 53 + 13

    def test_the_audit_trail_is_complete_and_unbroken(self, loaded_demo):
        assert verify_audit_chain().is_intact
        sample = Sample.objects.get(label="Sam_BFA148", kind=SampleKind.FIELD_SAMPLE)
        reasons = {
            event.pgh_context.metadata.get("reason")
            for event in sample.events.filter(pgh_label="update")
        }
        assert reasons == {loader.DEMO_PLACEMENT_REASON}
        assert sample.events.get(pgh_label="insert").pgh_context.metadata["user"] == str(
            User.objects.get(username="demo_technician").pk
        )

    def test_loading_twice_changes_nothing(self, loaded_demo):
        before = (Sample.objects.count(), CollectionEvent.objects.count())

        second = loader.load_demo_data()

        assert second.already_loaded
        assert (Sample.objects.count(), CollectionEvent.objects.count()) == before

    def test_demo_accounts_cannot_sign_in_unless_a_password_is_given(self, loaded_demo):
        assert not any(user.has_usable_password() for user in User.objects.all())

        loader.load_demo_data(password="a long demo password")

        assert User.objects.get(username="demo_viewer").check_password("a long demo password")

    def test_project_roles_are_those_of_the_demo_accounts(self, loaded_demo):
        project = Project.objects.get(code=loader.DEMO_PROJECT_CODE)

        assert {
            membership.user.username: membership.role for membership in project.memberships.all()
        } == {"demo_lead": "lead", "demo_technician": "contributor", "demo_viewer": "viewer"}


class TestSyntheticVolume:
    def test_generated_records_live_in_their_own_project_and_are_reproducible(self):
        users = loader._create_users(None)
        loader._load_synthetic(users, 25)
        synthetic = Project.objects.get(code=loader.SYNTHETIC_PROJECT_CODE)
        synthetic_samples = Sample.objects.filter(project=synthetic)
        first_run = sorted(synthetic_samples.values_list("label", "kind"))

        assert synthetic_samples.filter(kind=SampleKind.FIELD_SAMPLE).count() == 25
        assert all("Synthetic" in s.notes for s in synthetic_samples.filter(parent=None))
        assert all(label.startswith("SYN-") for label, kind in first_run if kind == "field_sample")
        assert Sample.objects.filter(project=synthetic).count() == len(first_run)


class TestCommand:
    def test_it_refuses_to_run_with_debug_off_unless_allowed(self, settings):
        settings.DEBUG = False

        with pytest.raises(CommandError, match="allow-production"):
            call_command("load_demo_data")

    def test_it_reports_what_it_created(self, settings):
        settings.DEBUG = True
        output = StringIO()
        summary = loader.LoadSummary(counts={"collection events": 3})

        with mock.patch(
            "poresira.samples.management.commands.load_demo_data.load_demo_data",
            return_value=summary,
        ) as run:
            call_command("load_demo_data", "--synthetic-volume", "5", stdout=output)

        run.assert_called_once_with(password=None, synthetic_volume=5)
        text = output.getvalue()
        assert "Demo data loaded." in text
        assert "3 collection events" in text
        assert "without a usable password" in text

    def test_debug_off_is_overridden_explicitly(self, settings):
        settings.DEBUG = False

        with mock.patch(
            "poresira.samples.management.commands.load_demo_data.load_demo_data",
            return_value=loader.LoadSummary(already_loaded=True),
        ):
            output = StringIO()
            call_command("load_demo_data", "--allow-production", stdout=output)

        assert "already present" in output.getvalue()
