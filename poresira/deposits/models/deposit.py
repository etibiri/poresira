# SPDX-License-Identifier: AGPL-3.0-or-later
import pghistory
import pgtrigger
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from poresira.core.models import HumanIdentifiedModel, Project, UUIDPrimaryKeyModel
from poresira.core.tracking import tracked
from poresira.core.voiding import VoidableModel, VoidableQuerySet
from poresira.samples.models import Sample
from poresira.sequencing.models import SequencingRun
from poresira.storage.models import DataFile

DEPOSIT_TYPE_CODE = "X"


class Archive(models.TextChoices):
    ENA = "ena", _("European Nucleotide Archive (ENA)")


class Environment(models.TextChoices):
    TEST = "test", _("Test service (discarded after a day)")
    PRODUCTION = "production", _("Production (public and permanent)")


class DepositStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    SUBMITTING = "submitting", _("Being submitted")
    ACCEPTED = "accepted", _("Accepted")
    REJECTED = "rejected", _("Rejected")


FINAL_STATUSES = (DepositStatus.ACCEPTED, DepositStatus.REJECTED)


class ItemKind(models.TextChoices):
    STUDY = "study", _("Study")
    SAMPLE = "sample", _("Sample")
    READS = "reads", _("Reads")


class ItemStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    ACCEPTED = "accepted", _("Accepted")
    REJECTED = "rejected", _("Rejected")


class DepositQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "DepositQuerySet":
        return self.filter(project__in=Project.objects.visible_to(user).values("pk"))


class DepositItemQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "DepositItemQuerySet":
        return self.filter(deposit__project__in=Project.objects.visible_to(user).values("pk"))


class DepositFileQuerySet(VoidableQuerySet):
    def visible_to(self, user) -> "DepositFileQuerySet":
        return self.filter(item__deposit__project__in=Project.objects.visible_to(user).values("pk"))


# Once a deposit leaves the draft, what is sent no longer changes; a finished deposit stays
# finished (send again as a new deposit).
DEPOSIT_LIFECYCLE_SQL = """
    IF OLD.status <> 'draft' AND (
        OLD.environment <> NEW.environment OR OLD.title <> NEW.title
        OR OLD.hold_until IS DISTINCT FROM NEW.hold_until
        OR OLD.sample_checklist <> NEW.sample_checklist
        OR OLD.library_source <> NEW.library_source
        OR OLD.library_selection <> NEW.library_selection
        OR OLD.library_strategy <> NEW.library_strategy
        OR OLD.compliance_confirmed_at IS DISTINCT FROM NEW.compliance_confirmed_at
    ) THEN
        RAISE EXCEPTION 'A submitted deposit keeps what was sent'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status IN ('accepted', 'rejected') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'A finished deposit stays finished'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""

# Items are added while the deposit is a draft, and belong to its project. A reads item
# needs a sample item of the same deposit.
ITEM_RULES_SQL = """
    IF NOT EXISTS (
        SELECT 1 FROM deposits_deposit WHERE id = NEW.deposit_id AND status = 'draft'
    ) THEN
        RAISE EXCEPTION 'Items are added while the deposit is a draft'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.sample_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM deposits_deposit d JOIN samples_sample s ON s.project_id = d.project_id
        WHERE d.id = NEW.deposit_id AND s.id = NEW.sample_id
    ) THEN
        RAISE EXCEPTION 'A deposited sample belongs to the project of the deposit'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.sequencing_run_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM deposits_deposit d JOIN sequencing_sequencingrun r
            ON r.project_id = d.project_id
        WHERE d.id = NEW.deposit_id AND r.id = NEW.sequencing_run_id
    ) THEN
        RAISE EXCEPTION 'A deposited run belongs to the project of the deposit'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind = 'reads' AND NOT EXISTS (
        SELECT 1 FROM deposits_deposititem p
        WHERE p.id = NEW.sample_item_id AND p.deposit_id = NEW.deposit_id AND p.kind = 'sample'
    ) THEN
        RAISE EXCEPTION 'Reads belong to a sample of the same deposit'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""

# An accession is given by the archive while the deposit is being submitted, and never changes.
ITEM_ACCESSION_SQL = """
    IF OLD.accession <> '' AND NEW.accession <> OLD.accession THEN
        RAISE EXCEPTION 'An accession never changes'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.accession <> OLD.accession OR NEW.secondary_accession <> OLD.secondary_accession THEN
        IF NOT EXISTS (
            SELECT 1 FROM deposits_deposit WHERE id = NEW.deposit_id AND status = 'submitting'
        ) THEN
            RAISE EXCEPTION 'Accessions are recorded while the deposit is being submitted'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
"""

FILE_RULES_SQL = """
    IF NOT EXISTS (
        SELECT 1 FROM deposits_deposititem i JOIN deposits_deposit d ON d.id = i.deposit_id
        JOIN storage_datafile f ON f.project_id = d.project_id
        WHERE i.id = NEW.item_id AND f.id = NEW.file_id
            AND i.kind = 'reads' AND d.status = 'draft'
            AND f.kind IN ('fastq', 'bam')
    ) THEN
        RAISE EXCEPTION 'Reads files are FASTQ or BAM files of the project, added to a draft'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
"""

# Patterns of the archive's accessions.
STUDY_ACCESSION = r"^PRJ[EDN][A-Z]\d+$"
SAMPLE_ACCESSION = r"^SAM[EDN][A-Z]?\d+$"
RUN_ACCESSION = r"^[EDS]RR\d+$"
EXPERIMENT_ACCESSION = r"^[EDS]RX\d+$"


@pghistory.track(*tracked())
class Deposit(UUIDPrimaryKeyModel, HumanIdentifiedModel, VoidableModel):
    """What a project sends to a public archive, and what the archive answered."""

    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="deposits", verbose_name=_("project")
    )
    archive = models.CharField(
        _("archive"), max_length=16, choices=Archive.choices, default=Archive.ENA
    )
    environment = models.CharField(
        _("environment"), max_length=16, choices=Environment.choices, default=Environment.TEST
    )
    status = models.CharField(
        _("status"), max_length=16, choices=DepositStatus.choices, default=DepositStatus.DRAFT
    )
    title = models.CharField(_("title"), max_length=300)
    description = models.TextField(_("description"), blank=True)
    hold_until = models.DateField(
        _("release date"),
        null=True,
        blank=True,
        help_text=_("The data stay private until this date. Required for the production service."),
    )
    sample_checklist = models.CharField(
        _("sample checklist"),
        max_length=20,
        default="ERC000011",
        help_text=_("The archive's checklist of sample attributes."),
    )
    library_source = models.CharField(_("library source"), max_length=40, default="GENOMIC")
    library_selection = models.CharField(_("library selection"), max_length=40, default="RANDOM")
    library_strategy = models.CharField(_("library strategy"), max_length=40, default="WGS")
    compliance_confirmed_at = models.DateTimeField(
        _("permits checked on"),
        null=True,
        blank=True,
        help_text=_("Sampling and benefit-sharing permits were checked before publication."),
    )
    compliance_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("permits checked by"),
    )
    webin_user = models.CharField(
        _("archive account"),
        max_length=100,
        blank=True,
        help_text=_("The account name used to submit. Its password is never stored."),
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("submitted by"),
    )
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)
    receipt = models.TextField(
        _("last receipt"),
        blank=True,
        help_text=_("What the archive answered, without credentials."),
    )
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DepositQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["-created_at"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.CheckConstraint(
                condition=models.Q(environment="test")
                | models.Q(hold_until__isnull=False, compliance_confirmed_at__isnull=False),
                name="production_deposit_has_release_date_and_permits",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft")
                | models.Q(submitted_at__isnull=False, submitted_by__isnull=False),
                name="deposit_sent_has_date_and_author",
            ),
            models.CheckConstraint(
                condition=~models.Q(status__in=["accepted", "rejected"])
                | models.Q(finished_at__isnull=False),
                name="deposit_finished_has_an_end_date",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_deposit_identity", fields=["identifier", "project", "archive"]
            ),
            pgtrigger.Trigger(
                name="check_deposit_lifecycle",
                when=pgtrigger.Before,
                operation=pgtrigger.Update,
                func=pgtrigger.Func(DEPOSIT_LIFECYCLE_SQL),
            ),
        ]

    def identifier_type_code(self) -> str:
        return DEPOSIT_TYPE_CODE

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES

    def __str__(self) -> str:
        return f"{self.identifier} {self.title}"


@pghistory.track(*tracked())
class DepositItem(UUIDPrimaryKeyModel, VoidableModel):
    """One object sent in a deposit: the study, a sample, or the reads of a sample."""

    deposit = models.ForeignKey(
        Deposit, on_delete=models.PROTECT, related_name="items", verbose_name=_("deposit")
    )
    kind = models.CharField(_("kind"), max_length=16, choices=ItemKind.choices)
    alias = models.CharField(
        _("alias"),
        max_length=100,
        help_text=_("The name the archive knows this object by, unique in the deposit."),
    )
    sample = models.ForeignKey(
        Sample,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="deposit_items",
        verbose_name=_("sample"),
    )
    sequencing_run = models.ForeignKey(
        SequencingRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="deposit_items",
        verbose_name=_("sequencing run"),
    )
    sample_item = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reads_items",
        verbose_name=_("sample of the archive"),
    )
    status = models.CharField(
        _("status"), max_length=16, choices=ItemStatus.choices, default=ItemStatus.PENDING
    )
    accession = models.CharField(_("accession"), max_length=20, blank=True)
    secondary_accession = models.CharField(_("experiment accession"), max_length=20, blank=True)
    message = models.TextField(_("message"), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DepositItemQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["deposit", "kind", "alias"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["deposit", "alias"],
                condition=models.Q(voided_at__isnull=True),
                name="one_active_item_per_alias",
            ),
            models.UniqueConstraint(
                fields=["deposit", "accession"],
                condition=models.Q(voided_at__isnull=True) & ~models.Q(accession=""),
                name="one_active_item_per_accession",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind="study", sample__isnull=True, sequencing_run__isnull=True)
                    & models.Q(sample_item__isnull=True)
                    | models.Q(kind="sample", sample__isnull=False, sequencing_run__isnull=True)
                    & models.Q(sample_item__isnull=True)
                    | models.Q(kind="reads", sample__isnull=False, sequencing_run__isnull=False)
                    & models.Q(sample_item__isnull=False)
                ),
                name="item_links_match_its_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(accession="")
                | models.Q(kind="study", accession__regex=STUDY_ACCESSION)
                | models.Q(kind="sample", accession__regex=SAMPLE_ACCESSION)
                | models.Q(kind="reads", accession__regex=RUN_ACCESSION),
                name="item_accession_format",
            ),
            models.CheckConstraint(
                condition=models.Q(secondary_accession="")
                | models.Q(kind="reads", secondary_accession__regex=EXPERIMENT_ACCESSION),
                name="item_experiment_accession_format",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="accepted") | ~models.Q(accession=""),
                name="accepted_item_has_an_accession",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(
                name="read_only_item_identity",
                fields=["deposit", "kind", "alias", "sample", "sequencing_run", "sample_item"],
            ),
            pgtrigger.Trigger(
                name="check_item_rules",
                when=pgtrigger.Before,
                operation=pgtrigger.Insert,
                func=pgtrigger.Func(ITEM_RULES_SQL),
            ),
            pgtrigger.Trigger(
                name="check_item_accession",
                when=pgtrigger.Before,
                operation=pgtrigger.Update,
                func=pgtrigger.Func(ITEM_ACCESSION_SQL),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.deposit_id} {self.kind} {self.alias}"


@pghistory.track(*tracked())
class DepositFile(UUIDPrimaryKeyModel, VoidableModel):
    """A reads file of a reads item, with the checksum it had when the deposit was prepared."""

    item = models.ForeignKey(
        DepositItem, on_delete=models.PROTECT, related_name="files", verbose_name=_("item")
    )
    file = models.ForeignKey(
        DataFile, on_delete=models.PROTECT, related_name="deposit_files", verbose_name=_("file")
    )
    sha256 = models.CharField(_("SHA-256 at preparation"), max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = DepositFileQuerySet.as_manager()

    class Meta(VoidableModel.Meta):
        ordering = ["item", "file"]
        constraints = [
            *VoidableModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["item", "file"],
                condition=models.Q(voided_at__isnull=True),
                name="one_active_file_per_item",
            ),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="deposit_file_sha256_format",
            ),
        ]
        triggers = [
            *VoidableModel.Meta.triggers,
            pgtrigger.ReadOnly(name="read_only_deposit_file", fields=["item", "file", "sha256"]),
            pgtrigger.Trigger(
                name="check_deposit_file_rules",
                when=pgtrigger.Before,
                operation=pgtrigger.Insert,
                func=pgtrigger.Func(FILE_RULES_SQL),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item_id} {self.file_id}"
