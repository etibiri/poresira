# SPDX-License-Identifier: AGPL-3.0-or-later
"""Register every file of a directory in place, for example the output folder of a run."""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError

from poresira.core.models import Project

from . import services
from .kinds import classify
from .models import DataFile, StorageBackend


class DirectoryError(Exception):
    """The directory cannot be registered in this backend."""


@dataclass
class Report:
    registered: list[DataFile] = field(default_factory=list)
    already_registered: int = 0
    filtered_out: int = 0
    would_register: int = 0
    would_register_bytes: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def registered_bytes(self) -> int:
        return sum(file.size_bytes for file in self.registered)


def _relative_paths(backend: StorageBackend, directory: Path) -> list[str]:
    root = Path(backend.root).resolve()
    directory = directory.resolve()
    if not directory.is_dir():
        raise DirectoryError(f"{directory} is not a directory.")
    if not directory.is_relative_to(root):
        raise DirectoryError(f"{directory} is not inside the backend {backend.name} ({root}).")
    paths = []
    for folder, subfolders, names in os.walk(directory):
        subfolders[:] = sorted(name for name in subfolders if not name.startswith("."))
        for name in sorted(names):
            if not name.startswith("."):
                paths.append((Path(folder) / name).relative_to(root).as_posix())
    return paths


def _describe(error: Exception) -> str:
    return "; ".join(error.messages) if isinstance(error, ValidationError) else str(error)


def register_directory(
    *,
    user,
    project: Project,
    backend: StorageBackend,
    directory: Path,
    link: dict,
    only_kinds: set[str] | None = None,
    dry_run: bool = False,
    progress: Callable[[str], None] = lambda line: None,
) -> Report:
    """Register each regular file under ``directory`` (hidden files excepted), once.

    Each file is its own transaction, so an interrupted run resumes where it stopped: files
    already registered at the same place are skipped. A dry run reads no file content.
    """
    if not backend.is_readable:
        raise DirectoryError("An offline medium cannot be scanned; register its files one by one.")
    report = Report()
    known = set(DataFile.objects.active().filter(backend=backend).values_list("path", flat=True))
    for path in _relative_paths(backend, directory):
        if only_kinds is not None and classify(path) not in only_kinds:
            report.filtered_out += 1
        elif path in known:
            report.already_registered += 1
        elif dry_run:
            report.would_register += 1
            report.would_register_bytes += (Path(backend.root) / path).stat().st_size
        else:
            try:
                file = services.register_file(
                    user=user, project=project, backend=backend, path=path, **link
                )
            except (ValidationError, PermissionDenied, services.StorageServiceError) as error:
                report.failed.append((path, _describe(error) or "refused"))
                progress(f"FAILED {path}")
            else:
                report.registered.append(file)
                progress(f"{file.identifier} {path}")
    return report
