# SPDX-License-Identifier: AGPL-3.0-or-later
"""How PoreSira reaches files. Each kind of backend is one ``FileStore``.

The open stores (a disk, a mounted share, catalogued offline media) are enough to run the
application. A future service is one more store: no feature may depend on one.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .models import BackendKind, StorageBackend

CHUNK_BYTES = 8 * 1024 * 1024


class FileNotReadableError(Exception):
    """The store cannot read files, as with an offline medium."""


@dataclass(frozen=True)
class Fingerprint:
    size_bytes: int
    sha256: str
    md5: str


class FileStore:
    readable = True

    def fingerprint(self, path: str) -> Fingerprint:
        """Size and checksums, read once. Raises ``FileNotFoundError`` or ``OSError``."""
        raise NotImplementedError


class DiskStore(FileStore):
    """A directory of this computer: a local disk or a mounted network share."""

    def __init__(self, root: str):
        self.root = Path(root)

    def locate(self, path: str) -> Path:
        """The file on disk; a path that leaves the root, links included, is refused."""
        located = (self.root / path).resolve()
        if not located.is_relative_to(self.root.resolve()):
            raise PermissionError(f"{path} is outside the backend")
        return located

    def fingerprint(self, path: str) -> Fingerprint:
        located = self.locate(path)
        sha256, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
        size = 0
        with located.open("rb") as file:
            while chunk := file.read(CHUNK_BYTES):
                sha256.update(chunk)
                md5.update(chunk)
                size += len(chunk)
        return Fingerprint(size, sha256.hexdigest(), md5.hexdigest())


class OfflineStore(FileStore):
    """Media on a shelf: files are catalogued with checksums given by the person, not read."""

    readable = False

    def fingerprint(self, path: str) -> Fingerprint:
        raise FileNotReadableError(path)


def store_for(backend: StorageBackend) -> FileStore:
    if backend.kind == BackendKind.OFFLINE:
        return OfflineStore()
    return DiskStore(backend.root)
