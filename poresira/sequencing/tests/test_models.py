# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.db import DatabaseError, IntegrityError, transaction

from poresira.core.audit import verify_audit_chain, with_reason
from poresira.core.identifiers import TYPE_CODE_PATTERN, is_valid_identifier
from poresira.core.models import AuditChainLink
from poresira.core.tests.factories import UserFactory
from poresira.core.voiding import HardDeleteForbiddenError
from poresira.samples.models import SampleKind
from poresira.samples.tests.factories import DerivativeFactory, SampleFactory
from poresira.sequencing.models import (
    LIBRARY_TYPE_CODE,
    SEQUENCING_RUN_TYPE_CODE,
    Library,
    LibraryMember,
    RunStatus,
    SequencingRun,
)
from poresira.sequencing.tests.factories import (
    BarcodeResultFactory,
    FlowCellFactory,
    FlowCellTypeFactory,
    InstrumentFactory,
    LibraryFactory,
    LibraryKitFactory,
    LibraryMemberFactory,
    SequencingRunFactory,
    run_of,
)

pytestmark = pytest.mark.django_db


def refuses(error, action):
    with pytest.raises(error), transaction.atomic():
        action()


class TestReferenceData:
    def test_a_kit_code_is_unique_among_active_kits_and_fixed(self):
        kit = LibraryKitFactory(code="SQK-NBD114.96")

        refuses(IntegrityError, lambda: LibraryKitFactory(code="SQK-NBD114.96"))
        kit.barcode_count = 24
        refuses(DatabaseError, kit.save)

    def test_a_barcode_count_must_be_positive(self):
        refuses(IntegrityError, lambda: LibraryKitFactory(barcode_count=0))

    def test_reference_data_is_voided_never_deleted(self):
        instrument = InstrumentFactory()

        with pytest.raises(HardDeleteForbiddenError):
            instrument.delete()
        instrument.void(user=UserFactory(), reason="Decommissioned")
        assert instrument.is_voided


class TestIdentifiers:
    def test_libraries_and_runs_get_valid_identifiers(self):
        library, run = LibraryFactory(), SequencingRunFactory()

        for record, code in ((library, LIBRARY_TYPE_CODE), (run, SEQUENCING_RUN_TYPE_CODE)):
            assert is_valid_identifier(record.identifier)
            assert TYPE_CODE_PATTERN.match(code)
            assert f"-{code}" in record.identifier


class TestLibrary:
    def test_identity_is_read_only(self):
        library = LibraryFactory()
        library.kit = LibraryKitFactory()

        refuses(DatabaseError, library.save)

    def test_a_barcode_is_used_once_per_library(self):
        member = LibraryMemberFactory(barcode=5)

        refuses(
            IntegrityError,
            lambda: LibraryMemberFactory(library=member.library, barcode=5),
        )

    def test_a_voided_member_frees_its_barcode(self):
        member = LibraryMemberFactory(barcode=5)
        member.void(user=UserFactory(), reason="Wrong tube")

        assert LibraryMemberFactory(library=member.library, barcode=5)

    def test_a_sample_is_in_a_library_once(self):
        member = LibraryMemberFactory(barcode=1)

        refuses(
            IntegrityError,
            lambda: LibraryMemberFactory(library=member.library, sample=member.sample, barcode=2),
        )

    def test_the_barcode_must_fit_the_kit(self):
        library = LibraryFactory(kit=LibraryKitFactory(barcode_count=24))

        assert LibraryMemberFactory(library=library, barcode=24)
        refuses(DatabaseError, lambda: LibraryMemberFactory(library=library, barcode=25))

    def test_a_kit_without_barcodes_takes_no_barcode(self):
        library = LibraryFactory(kit=LibraryKitFactory(barcode_count=None))

        refuses(DatabaseError, lambda: LibraryMemberFactory(library=library, barcode=1))

    def test_a_barcode_may_be_missing_only_in_a_reconstructed_library(self):
        refuses(DatabaseError, lambda: LibraryMemberFactory(barcode=None))
        library = LibraryFactory(reconstructed=True)

        assert LibraryMemberFactory(library=library, barcode=None).barcode is None

    def test_members_come_from_the_project_and_are_physical_material(self):
        library = LibraryFactory()
        foreign = DerivativeFactory(kind=SampleKind.DNA_EXTRACT)
        field_sample = SampleFactory(project=library.project)

        refuses(DatabaseError, lambda: LibraryMemberFactory(library=library, sample=foreign))
        refuses(DatabaseError, lambda: LibraryMemberFactory(library=library, sample=field_sample))

    def test_a_sealed_library_is_closed_to_new_members(self):
        member = LibraryMemberFactory()
        Library.objects.filter(pk=member.library_id).update(status="sealed")

        refuses(DatabaseError, lambda: LibraryMemberFactory(library=member.library))
        member.barcode = 40
        refuses(DatabaseError, member.save)

    def test_the_sample_of_a_member_cannot_change(self):
        member = LibraryMemberFactory()
        member.sample = DerivativeFactory(
            kind=SampleKind.DNA_EXTRACT, parent=SampleFactory(project=member.library.project)
        )

        refuses(DatabaseError, member.save)

    def test_quantities_must_be_positive(self):
        refuses(IntegrityError, lambda: LibraryMemberFactory(input_ng=0))


class TestFlowCellAndRun:
    def test_a_flow_cell_id_is_unique_and_fixed(self):
        cell = FlowCellFactory(flow_cell_id="PAW12345")

        refuses(IntegrityError, lambda: FlowCellFactory(flow_cell_id="PAW12345"))
        cell.flow_cell_id = "PAW00000"
        refuses(DatabaseError, cell.save)

    def test_a_run_needs_a_sealed_library_of_its_own_project(self):
        draft = LibraryFactory()
        sealed = LibraryFactory(status="sealed")

        refuses(DatabaseError, lambda: SequencingRunFactory(library=draft, project=draft.project))
        refuses(
            DatabaseError,
            lambda: SequencingRunFactory(library=sealed, project=LibraryFactory().project),
        )

    def test_a_flow_cell_carries_one_run_at_a_time(self):
        run = SequencingRunFactory()

        refuses(
            IntegrityError,
            lambda: SequencingRunFactory(library=run.library, flow_cell=run.flow_cell),
        )
        run.void(user=UserFactory(), reason="Aborted before loading")
        assert SequencingRunFactory(library=run.library, flow_cell=run.flow_cell)

    def test_a_reusable_flow_cell_serves_again_after_its_run_ends(self):
        run = SequencingRunFactory(status=RunStatus.COMPLETED)

        second = SequencingRunFactory(library=run.library, flow_cell=run.flow_cell)
        assert second.flow_cell_id == run.flow_cell_id

    def test_a_single_use_flow_cell_serves_one_run_unless_it_was_aborted(self):
        cell = FlowCellFactory(type=FlowCellTypeFactory(reusable=False))
        first = SequencingRunFactory(flow_cell=cell, status=RunStatus.COMPLETED)

        refuses(
            DatabaseError,
            lambda: SequencingRunFactory(library=first.library, flow_cell=cell),
        )
        with with_reason("Data lost"):
            SequencingRun.objects.filter(pk=first.pk).update(status=RunStatus.ABORTED)
        assert SequencingRunFactory(library=first.library, flow_cell=cell)

    def test_the_sequencer_run_id_is_unique_when_given(self):
        run = SequencingRunFactory(run_id="a1b2c3")
        SequencingRunFactory(run_id="")
        SequencingRunFactory(run_id="")

        refuses(IntegrityError, lambda: SequencingRunFactory(run_id=run.run_id))

    def test_identity_is_read_only(self):
        run = SequencingRunFactory()
        run.flow_cell = FlowCellFactory()

        refuses(DatabaseError, run.save)

    def test_a_completed_run_reopens_only_with_a_reason(self):
        run = SequencingRunFactory(status=RunStatus.COMPLETED)
        run.status = RunStatus.RUNNING

        refuses(DatabaseError, run.save)
        with with_reason("Wrong basecalling model recorded"):
            run.save()


class TestBarcodeResult:
    def test_a_result_belongs_to_a_member_of_the_library_of_the_run(self):
        result = BarcodeResultFactory()
        other_member = LibraryMemberFactory()

        assert result.member.library_id == result.run.library_id
        refuses(
            DatabaseError,
            lambda: BarcodeResultFactory(run=result.run, member=other_member),
        )

    def test_one_active_result_per_member(self):
        result = BarcodeResultFactory()

        refuses(
            IntegrityError,
            lambda: BarcodeResultFactory(run=result.run, member=result.member),
        )

    def test_results_of_a_completed_run_change_only_with_a_reason(self):
        result = BarcodeResultFactory()
        SequencingRun.objects.filter(pk=result.run_id).update(status=RunStatus.COMPLETED)
        result.reads = 2000

        refuses(DatabaseError, result.save)
        with with_reason("Recounted after demultiplexing again"):
            result.save()

    def test_results_of_a_running_run_change_freely(self):
        result = BarcodeResultFactory()
        result.reads = 2000
        result.save()

        assert type(result).objects.get(pk=result.pk).reads == 2000

    def test_nothing_is_ever_deleted(self):
        result = BarcodeResultFactory()

        with pytest.raises(HardDeleteForbiddenError):
            result.delete()
        refuses(
            HardDeleteForbiddenError,
            lambda: LibraryMember.objects.filter(pk=result.member_id).delete(),
        )


class TestAudit:
    def test_changes_are_audited_and_the_chain_is_intact(self):
        member = LibraryMemberFactory()
        run = run_of(member.library)

        assert member.events.filter(pgh_label="insert").count() == 1
        assert run.events.filter(pgh_label="insert").count() == 1
        assert AuditChainLink.objects.exists()
        assert verify_audit_chain().is_intact
