# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every change to the file registry goes through here: permissions, rules, reasons and audit.

Files are registered where they are and never copied or moved. A registered file keeps the
size and checksums seen at registration; a later check compares the disk with them.
"""

from contextlib import nullcontext
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from poresira.core.models import Project
from poresira.core.permissions import can_edit_project
from poresira.core.reason import acting_as, with_reason
from poresira.pcr.models import PcrRun
from poresira.samples.models import Sample
from poresira.sequencing.models import SequencingRun

from .backends import FileNotReadableError, store_for
from .kinds import classify
from .models import BackendKind, DataFile, FixityCheck, Integrity, StorageBackend, Tier
from .permissions import can_manage_reference_data

LINK_NAMES = ("sequencing_run", "pcr_run", "sample")
HEX_DIGITS = frozenset("0123456789abcdef")


class StorageServiceError(Exception):
    """A rule of the storage module was not respected."""


class RecordInUseError(StorageServiceError):
    """Raised when voiding a record that active records still depend on."""


def _require_editor(user, project: Project) -> None:
    if not can_edit_project(user, project):
        raise PermissionDenied


def _require_reference_manager(user) -> None:
    if not can_manage_reference_data(user):
        raise PermissionDenied


def _save_new(instance) -> None:
    instance.full_clean(exclude=["identifier"])
    instance.save()


def _acting(user):
    return acting_as(user) if user is not None else nullcontext()


# --- Backends ---------------------------------------------------------------------------------


def _clean_root(kind: str, root: str) -> str:
    root = root.strip()
    if kind == BackendKind.OFFLINE:
        if not root:
            raise ValidationError({"root": _("Give the label of the medium.")})
        return root
    if not root.startswith("/"):
        raise ValidationError({"root": _("Give an absolute directory, such as /data/nanopore.")})
    root = root.rstrip("/") or "/"
    if not Path(root).is_dir():
        raise ValidationError({"root": _("This directory does not exist on this computer.")})
    return root


@transaction.atomic
def create_backend(*, user, name: str, kind: str, root: str, notes: str = "") -> StorageBackend:
    _require_reference_manager(user)
    backend = StorageBackend(
        name=name.strip(), kind=kind, root=_clean_root(kind, root), notes=notes
    )
    with _acting(user):
        _save_new(backend)
    return backend


@transaction.atomic
def move_backend(*, user, backend: StorageBackend, root: str, reason: str) -> StorageBackend:
    """The files stay where they are in the backend; its root now points elsewhere."""
    _require_reference_manager(user)
    backend.root = _clean_root(backend.kind, root)
    with _acting(user), with_reason(reason):
        backend.full_clean()
        backend.save()
    return backend


# --- Files ------------------------------------------------------------------------------------


def _clean_path(path: str) -> str:
    path = path.strip()
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise ValidationError(
            {"path": _("Give a path relative to the backend, without a leading slash or “..”.")}
        )
    return path


def _clean_link(project: Project, links: dict):
    given = {name: record for name, record in links.items() if record is not None}
    if len(given) != 1:
        raise ValidationError(
            _("Attach the file to one sequencing run, one PCR run or one sample.")
        )
    name, record = next(iter(given.items()))
    if record.is_voided:
        raise ValidationError({name: _("A voided record cannot receive files.")})
    if record.project_id != project.pk:
        raise ValidationError({name: _("The record must belong to the project of the file.")})
    return name, record


def _checksum(name: str, value: str, length: int) -> str:
    value = value.strip().lower()
    if len(value) != length or not set(value) <= HEX_DIGITS:
        raise ValidationError(
            {name: _("Give %(length)s hexadecimal characters.") % {"length": length}}
        )
    return value


@transaction.atomic
def register_file(
    *,
    user,
    project: Project,
    backend: StorageBackend,
    path: str,
    sequencing_run: SequencingRun | None = None,
    pcr_run: PcrRun | None = None,
    sample: Sample | None = None,
    kind: str = "",
    notes: str = "",
    size_bytes: int | None = None,
    sha256: str = "",
    md5: str = "",
) -> DataFile:
    """Record a file where it is. On a disk or share it is read and hashed here; for an offline
    medium the size and checksums come from the person, who read them when the medium was made."""
    _require_editor(user, project)
    if backend.is_voided:
        raise ValidationError({"backend": _("A voided backend cannot be used.")})
    path = _clean_path(path)
    link_name, record = _clean_link(
        project, {"sequencing_run": sequencing_run, "pcr_run": pcr_run, "sample": sample}
    )
    store = store_for(backend)
    if store.readable:
        try:
            fingerprint = store.fingerprint(path)
        except FileNotFoundError as error:
            raise ValidationError({"path": _("No such file in this backend.")}) from error
        except OSError as error:
            raise ValidationError({"path": _("This file cannot be read.")}) from error
        size_bytes, sha256, md5 = fingerprint.size_bytes, fingerprint.sha256, fingerprint.md5
        integrity, checked_at = Integrity.INTACT, timezone.now()
    else:
        if size_bytes is None:
            raise ValidationError({"size_bytes": _("Give the size of the file.")})
        sha256, md5 = _checksum("sha256", sha256, 64), _checksum("md5", md5, 32)
        integrity, checked_at = Integrity.UNCHECKED, None
    file = DataFile(
        project=project,
        backend=backend,
        path=path,
        kind=kind or classify(path),
        size_bytes=size_bytes,
        sha256=sha256,
        md5=md5,
        integrity=integrity,
        last_checked_at=checked_at,
        registered_by=user,
        notes=notes,
        **{link_name: record},
    )
    with _acting(user):
        _save_new(file)
    return file


@transaction.atomic
def verify_file(*, file: DataFile, user=None) -> FixityCheck:
    """Compare the file on disk with what was recorded. ``user`` is empty for scheduled checks."""
    if user is not None:
        _require_editor(user, file.project)
    if file.is_voided:
        raise StorageServiceError(_("A voided file cannot be checked."))
    store = store_for(file.backend)
    if not store.readable:
        raise FileNotReadableError(_("A file on an offline medium cannot be checked here."))
    observed_size, observed_sha256, notes = None, "", ""
    try:
        fingerprint = store.fingerprint(file.path)
    except FileNotFoundError:
        outcome = Integrity.MISSING
    except OSError as error:
        outcome, notes = Integrity.UNREADABLE, str(error)
    else:
        observed_size, observed_sha256 = fingerprint.size_bytes, fingerprint.sha256
        same = (observed_size, observed_sha256) == (file.size_bytes, file.sha256)
        outcome = Integrity.INTACT if same else Integrity.MISMATCH
    check = FixityCheck(
        file=file,
        checked_by=user,
        outcome=outcome,
        observed_sha256=observed_sha256,
        observed_size_bytes=observed_size,
        notes=notes,
    )
    with _acting(user):
        _save_new(check)
        file.integrity, file.last_checked_at = outcome, check.checked_at
        file.save(update_fields=["integrity", "last_checked_at", "updated_at"])
    return check


@transaction.atomic
def change_tier(*, user, file: DataFile, tier: str, reason: str) -> DataFile:
    """Hot, warm or cold is a label the laboratory keeps; nothing is moved."""
    _require_editor(user, file.project)
    if file.is_voided:
        raise StorageServiceError(_("A voided file cannot be changed."))
    if tier not in Tier.values:
        raise ValidationError({"tier": _("Unknown tier.")})
    file.tier = tier
    with _acting(user), with_reason(reason):
        file.save(update_fields=["tier", "updated_at"])
    return file


# --- Voiding ----------------------------------------------------------------------------------


@transaction.atomic
def void_record(*, user, record, reason: str):
    """Void a file, or a backend that holds no active file."""
    if isinstance(record, DataFile):
        _require_editor(user, record.project)
    else:
        _require_reference_manager(user)
        if record.files.active().exists():
            raise RecordInUseError(_("Active files are registered in this backend."))
    with _acting(user):
        record.void(user=user, reason=reason)
    return record
