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
from poresira.pcr.models import Assay, PcrReaction, PcrRun, Primer, Result, RunStatus
from poresira.pcr.services import Validity, assess_run
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


class TestPcrData:
    def test_table_3_pcr_results_are_complete_and_reproduce_the_published_counts(self):
        rows = loader._read("article_table3_pcr_results.csv")

        assert len(rows) == 50
        positives = {
            column: sum(row[column] == "positive" for row in rows)
            for column in ("pcr_spfmv", "pcr_spcsv", "pcr_splcv")
        }
        assert positives == {"pcr_spfmv": 25, "pcr_spcsv": 21, "pcr_splcv": 19}
        triple = [r for r in rows if r["sample"] in ("BFA173", "BFA169", "BFA148")]
        assert all(r["pcr_spfmv"] == r["pcr_spcsv"] == r["pcr_splcv"] == "positive" for r in triple)

    def test_the_primers_are_those_of_the_paper_with_the_orientation_checked(self, loaded_demo):
        sequences = {p.name: (p.sequence, p.direction) for p in Primer.objects.all()}

        assert sequences["CP-F"] == ("ATGGCTGATAGCACTAAAGTCGA", "forward")
        assert sequences["CP-R"] == ("TCAACAGTGAAGACCTGTTCCAG", "reverse")
        assert sequences["CP1S"][1] == "forward"
        assert sequences["CP1A"][1] == "reverse"
        assert sequences["SPG1"] == ("CCCCKGTGCGWRAATCCAT", "forward")
        assert sequences["SPG2"][1] == "reverse"

    def test_each_assay_carries_its_published_protocol(self, loaded_demo):
        spfmv = Assay.objects.get(name="SPFMV CP RT-PCR")
        splcv = Assay.objects.get(name="SPLCV CP PCR")

        assert spfmv.reverse_transcription
        assert not splcv.reverse_transcription
        assert [str(s.temperature_c) for s in spfmv.cycling_program.steps.all()] == [
            "94.0",
            "94.0",
            "56.3",
            "72.0",
            "72.0",
        ]
        assert splcv.cycling_program.cycles == 30
        assert splcv.master_mix.components.count() == 6

    def test_three_reconstructed_runs_hold_the_150_published_results(self, loaded_demo):
        runs = PcrRun.objects.filter(notes__startswith="Results transcribed")

        assert runs.count() == 3
        assert all(r.reconstructed and r.status == RunStatus.CLOSED for r in runs)
        assert all(r.performed_on is None and r.reactions.count() == 50 for r in runs)
        assert all(assess_run(r).state == Validity.NOT_ASSESSED for r in runs)
        assert loaded_demo.counts["PCR reactions"] == 150 + 98
        positives = PcrReaction.objects.filter(
            result=Result.POSITIVE, run__notes__startswith="Results transcribed"
        )
        assert positives.filter(run__assay__name="SPFMV CP RT-PCR").count() == 25
        assert positives.filter(run__assay__name="SPCSV CP RT-PCR").count() == 21
        assert positives.filter(run__assay__name="SPLCV CP PCR").count() == 19

    def test_a_sample_shows_its_three_results(self, loaded_demo):
        sample = Sample.objects.get(kind=SampleKind.FIELD_SAMPLE, label="Sam_BFA148")

        results = {r.run.assay.name: r.result for r in sample.pcr_reactions.all()}

        assert results == {
            "SPFMV CP RT-PCR": "positive",
            "SPCSV CP RT-PCR": "positive",
            "SPLCV CP PCR": "positive",
        }

    def test_samples_without_a_biosample_are_tested_under_their_table_number(self, loaded_demo):
        sample = Sample.objects.get(kind=SampleKind.FIELD_SAMPLE, label="BFA44")

        assert sample.pcr_reactions.count() == 3
        assert {r.result for r in sample.pcr_reactions.all()} == {"negative"}


@pytest.fixture(scope="module")
def rows():
    with (loader.DATA_DIR / "thesis_98_samples.csv").open(encoding="utf-8") as file:
        return list(csv.DictReader(file))


class TestLaboratoryFile:
    def test_it_holds_the_98_samples_and_the_published_selection(self, rows):
        assert len(rows) == 98 and len({r["sample_code"] for r in rows}) == 98
        assert sum(r["spg_pcr"] == "positive" for r in rows) == 53
        assert sum(r["sequenced"] == "yes" for r in rows) == 53
        assert all(r["collected_on"].startswith("2016") for r in rows)

    def test_every_sequenced_sample_had_a_successful_rca(self, rows):
        for row in rows:
            if row["sequenced"] == "yes":
                assert "positive" in (row["templiphi_result"], row["equiphi29_result"])

    def test_coordinates_are_no_more_precise_than_ncbi(self, rows):
        for row in rows:
            for name in ("latitude", "longitude"):
                assert len(row[name].split(".")[1]) == 2

    def test_it_agrees_with_the_biosamples_on_dates_and_sequenced_samples(self, rows):
        biosamples = {
            loader.split_isolate(r["isolate"])[1]: r
            for r in loader._read("biosamples_PRJNA1460897.csv")
            if r["collection_date"].startswith("2016")
        }
        for row in rows:
            biosample = biosamples.get(row["sample_code"].removeprefix("BFA"))
            if biosample:
                assert biosample["collection_date"] == row["collected_on"]
            assert (biosample is not None) == (row["sequenced"] == "yes")


class TestLaboratoryData:
    def test_extracts_carry_their_nanodrop_readings(self, loaded_demo):
        extract = Sample.objects.get(kind=SampleKind.DNA_EXTRACT, label="Sam_BFA148")
        reading = extract.qc_measurements.get()

        assert reading.method == "nanodrop"
        assert (str(reading.concentration_ng_ul), str(reading.a260_280)) == ("284.400", "1.98")
        assert extract.concentration_ng_ul == reading.concentration_ng_ul

    def test_each_rca_attempt_is_a_product_with_its_gel_result(self, loaded_demo):
        done = Sample.objects.filter(kind=SampleKind.AMPLIFICATION_PRODUCT)
        assert done.filter(qc_measurements__outcome="passed").count() == 51 + 2 + 1
        assert done.filter(qc_measurements__outcome="failed").count() == 86 - 54

    def test_the_sequenced_product_of_bfa316_is_the_equiphi29_one(self, loaded_demo):
        from poresira.sequencing.models import LibraryMember

        member = LibraryMember.objects.get(sample__label__startswith="Tie_BFA316")

        assert member.sample.label == "Tie_BFA316 [EquiPhi29]"
        assert "Sequenced" in member.sample.notes
        other = Sample.objects.get(label="Tie_BFA316 [TempliPhi]")
        assert "Sequenced" not in other.notes

    def test_when_both_kits_were_tried_the_label_names_the_kit(self, loaded_demo):
        labels = set(
            Sample.objects.filter(
                kind=SampleKind.AMPLIFICATION_PRODUCT, label__contains="["
            ).values_list("label", flat=True)
        )
        assert "Sam_BFA81 [TempliPhi]" in labels and "Sam_BFA81 [EquiPhi29]" in labels

    def test_the_sequence_is_linked_to_the_rca_that_worked(self, loaded_demo):
        for record in SequenceRecord.objects.filter(sample__kind=SampleKind.AMPLIFICATION_PRODUCT):
            assert record.sample.qc_measurements.get().outcome == "passed"

    def test_two_runs_hold_the_98_sweepovirus_results(self, loaded_demo):
        runs = PcrRun.objects.filter(notes__startswith="Sweepovirus detection")

        assert [r.tube_count for r in runs.order_by("created_at")] == [49, 49]
        assert all(r.reconstructed and r.status == RunStatus.CLOSED for r in runs)
        reactions = PcrReaction.objects.filter(run__in=runs)
        assert reactions.count() == 98
        assert reactions.filter(result=Result.POSITIVE).count() == 53
        assert {r.sample.kind for r in reactions} == {SampleKind.DNA_EXTRACT}

    def test_places_are_written_without_accents_and_gh_means_near_ghana(self, loaded_demo):
        event = Sample.objects.get(
            kind=SampleKind.FIELD_SAMPLE, label="Bou_BFA1274"
        ).collection_event
        assert event.site_name == "Boura"
        assert "Ghana" in event.notes
        tiebele = Sample.objects.get(kind=SampleKind.FIELD_SAMPLE, label="Tie_BFA266")
        assert tiebele.collection_event.province == "Nahouri"  # Table 3 keeps its own spelling

    def test_coordinates_are_rounded_to_a_hundredth_of_a_degree(self, loaded_demo):
        event = Sample.objects.get(
            kind=SampleKind.FIELD_SAMPLE, label="Sam_BFA148"
        ).collection_event
        assert (str(event.latitude), str(event.longitude)) == ("10.94000", "-5.11000")
        assert event.site_name == "Samogohiri"


class TestLoading:
    def test_the_published_sample_counts_are_reproduced(self, loaded_demo):
        assert not loaded_demo.already_loaded
        assert kind_count(SampleKind.DNA_EXTRACT) == 98
        assert kind_count(SampleKind.AMPLIFICATION_PRODUCT) == 75 + 11  # one per RCA attempt
        assert kind_count(SampleKind.RNA_EXTRACT) == 13
        assert kind_count(SampleKind.FIELD_SAMPLE) == 98 + 43 + 13
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

    def test_a_date_that_disagrees_with_the_biosample_follows_the_biosample(self, loaded_demo):
        record = SequenceRecord.objects.get(accession="PV405577")

        assert record.sample.label == "Ban_BFA1094"
        assert "2016-08-18" in record.notes
        assert "BioSample date is the correct one" in record.notes
        assert record.sample.collection_event.collected_on.isoformat() == "2016-08-16"

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
        assert placed.count() == 98 + 86 + 13
        assert len({(s.storage_location_id, s.position) for s in placed}) == placed.count()
        assert Sample.objects.filter(status=SampleStatus.STORED).count() == 154 + 98 + 86 + 13

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

    def test_the_sequencing_reference_data_and_flow_cells_are_loaded_once(self, loaded_demo):
        from poresira.sequencing.models import FlowCell, FlowCellType, LibraryKit

        assert set(LibraryKit.objects.values_list("code", flat=True)) == {
            "SQK-NBD114.96",
            "SQK-RBK114.96",
            "SQK-PCB114.24",
            "SQK-PSK004",
        }
        assert not FlowCellType.objects.get(code="FLO-FLG001").reusable
        assert FlowCellType.objects.get(code="FLO-PRO114M").reusable
        assert set(FlowCell.objects.values_list("flow_cell_id", flat=True)) == {
            "FAT93267",
            "FAV02927",
            "AMF735",
        }
        loader.load_demo_data()
        assert FlowCell.objects.count() == 3

    def test_the_flongle_run_of_bfa316_is_recorded_from_the_minknow_files(self, loaded_demo):
        from poresira.sequencing.models import RunStatus, SequencingRun

        run = SequencingRun.objects.get(flow_cell__flow_cell_id="AMF735")
        member = run.library.members.get()

        assert run.status == RunStatus.COMPLETED
        assert run.flow_cell.flow_cell_id == "AMF735"
        assert not run.flow_cell.type.reusable
        assert run.instrument.name == "MinION Mk1C MC-111618"
        assert (run.started_on.isoformat(), run.basecaller_version) == ("2022-11-04", "5.1.13")
        assert member.sample.label == "Tie_BFA316 [EquiPhi29]"
        assert member.barcode is None
        assert run.results.get().reads == run.total_reads == 530760

    def test_the_two_minion_runs_hold_the_52_samples_of_the_barcode_sheet(self, loaded_demo):
        from poresira.sequencing.models import LibraryMember, SequencingRun

        first = SequencingRun.objects.get(flow_cell__flow_cell_id="FAT93267")
        second = SequencingRun.objects.get(flow_cell__flow_cell_id="FAV02927")

        assert first.library.members.count() == 24
        assert second.library.members.count() == 28
        assert first.run_id == "20231109_1221_mc-111618_fat93267_14bc03be"
        assert (first.started_on.isoformat(), second.started_on.isoformat()) == (
            "2023-11-09",
            "2023-11-11",
        )
        assert first.basecaller_version == "0.9.0"
        last = LibraryMember.objects.get(library=second.library, barcode=52)
        assert last.sample.label.startswith("BFA1368") or "BFA1368" in last.sample.label
        assert LibraryMember.objects.count() == 53
        # every sequenced sample of the laboratory file is in exactly one library
        assert LibraryMember.objects.values("sample").distinct().count() == 53

    def test_the_fictitious_storage_folder_is_written_and_registered_once(
        self, loaded_demo, tmp_path
    ):
        from poresira.pcr.models import PcrRun
        from poresira.sequencing.models import SequencingRun
        from poresira.storage.demo import NOTICE, create_demo_storage
        from poresira.storage.models import DataFile, FileKind, Integrity, StorageBackend

        platform = User.objects.get(username="demo_platform")
        technician = User.objects.get(username="demo_technician")

        first = create_demo_storage(tmp_path, platform=platform, technician=technician)
        second = create_demo_storage(tmp_path, platform=platform, technician=technician)

        on_disk = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert first.written == len(on_disk) and second.written == 0 and second.registered == 0
        assert DataFile.objects.count() == len(on_disk) == first.registered
        assert StorageBackend.objects.get().root == str(tmp_path)
        assert sum(path.stat().st_size for path in on_disk) < 3_000_000
        assert {f.integrity for f in DataFile.objects.all()} == {Integrity.INTACT}
        kinds = set(DataFile.objects.values_list("kind", flat=True))
        assert {FileKind.POD5, FileKind.FASTQ, FileKind.BAM, FileKind.GEL_IMAGE} <= kinds
        first_run = SequencingRun.objects.get(flow_cell__flow_cell_id="FAT93267")
        assert first_run.files.filter(path__contains="barcode24").exists()
        gels = PcrRun.objects.filter(notes__startswith="Sweepovirus detection")
        assert sorted(run.files.count() for run in gels) == [4, 4]
        assert (tmp_path / "computing").is_dir()
        assert NOTICE.encode() in (tmp_path / "gels" / "Gel1.jpg").read_bytes()

    def test_the_accessions_the_public_archives_already_hold_are_recorded(self, loaded_demo):
        from poresira.deposits.known import known_bioproject, known_for
        from poresira.deposits.models import AccessionKind, PublicAccession
        from poresira.deposits.tests.factories import PublicAccessionFactory  # noqa: F401

        project = Project.objects.get(code=loader.DEMO_PROJECT_CODE)
        records = PublicAccession.objects.filter(project=project)

        assert known_bioproject(project) == "PRJNA1460897"
        assert records.filter(kind=AccessionKind.BIOSAMPLE).count() == 66
        assert records.filter(kind=AccessionKind.SRA_RUN).count() == 66
        assert records.filter(kind=AccessionKind.SRA_EXPERIMENT).count() == 66
        sample = Sample.objects.get(kind=SampleKind.FIELD_SAMPLE, label="Tie_BFA316")
        known = known_for(sample)
        assert known.biosample == "SAMN57660728"
        assert known.sra_runs and known.sra_runs[0].startswith("SRR")
        assert known.genbank == ("PV947756",)
        assert all(r.archive_name == "NCBI" for r in records)

    def test_loading_the_accessions_twice_adds_nothing(self, loaded_demo):
        from poresira.deposits.models import PublicAccession
        from poresira.samples.demo.public import load_public_accessions

        project = Project.objects.get(code=loader.DEMO_PROJECT_CODE)
        users = {"demo_technician": User.objects.get(username="demo_technician")}
        before = PublicAccession.objects.count()

        assert load_public_accessions(users, project) == 0
        assert PublicAccession.objects.count() == before == 1 + 66 * 3

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
