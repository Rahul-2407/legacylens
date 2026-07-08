"""Archive safety inspection.

Everything here runs BEFORE extraction. The threat model treats every
uploaded archive as hostile:

* zip-slip / path traversal ("../../etc/cron.d/evil")
* absolute paths ("/etc/passwd") and Windows drive prefixes ("C:\\...")
* symlink members (escape the workspace after extraction)
* decompression bombs (tiny archive, enormous declared payload)
* resource exhaustion (declared size / file count over configured ceilings)

Declared sizes in zip headers can lie, so inspection is necessary but not
sufficient — the extractor additionally enforces a streaming byte cap on
what is actually written (see extractor.py).
"""

import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from legacylens.core.config import Settings
from legacylens.core.exceptions import (
    ArchiveBombError,
    ArchiveTooLargeError,
    IngestionError,
    UnsafeArchiveError,
)

# Ratio checks only apply above this size: legitimately tiny archives of
# highly compressible text would otherwise false-positive.
MIN_BOMB_SIZE_BYTES = 10 * 1024 * 1024

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class ArchiveStats:
    file_count: int
    total_compressed_bytes: int
    total_declared_bytes: int


def normalize_member_path(raw_name: str) -> PurePosixPath:
    """Normalize a zip member name and reject unsafe forms."""
    name = raw_name.replace("\\", "/")
    if name.startswith("/") or _WINDOWS_DRIVE.match(name):
        raise UnsafeArchiveError(f"Absolute path in archive: '{raw_name}'")
    path = PurePosixPath(name)
    if ".." in path.parts:
        raise UnsafeArchiveError(f"Path traversal in archive: '{raw_name}'")
    return path


def _is_symlink_member(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def inspect_zip(archive_path: Path, settings: Settings) -> ArchiveStats:
    """Validate an archive against the full safety policy.

    Returns aggregate stats on success; raises a specific IngestionError
    subclass on the first violation found.
    """
    if not zipfile.is_zipfile(archive_path):
        raise IngestionError(f"Not a valid zip archive: {archive_path.name}")

    archive_bytes = archive_path.stat().st_size
    max_archive = settings.max_archive_size_mb * 1024 * 1024
    if archive_bytes > max_archive:
        raise ArchiveTooLargeError(
            f"Archive is {archive_bytes} bytes; limit is {max_archive}"
        )

    file_count = 0
    total_compressed = 0
    total_declared = 0

    with zipfile.ZipFile(archive_path) as zf:
        for info in zf.infolist():
            normalize_member_path(info.filename)
            if _is_symlink_member(info):
                raise UnsafeArchiveError(
                    f"Symlink member in archive: '{info.filename}'"
                )
            if info.is_dir():
                continue
            file_count += 1
            total_compressed += info.compress_size
            total_declared += info.file_size

    if file_count > settings.max_file_count:
        raise ArchiveTooLargeError(
            f"Archive declares {file_count} files; "
            f"limit is {settings.max_file_count}"
        )

    max_extracted = settings.max_extracted_size_mb * 1024 * 1024
    if total_declared > max_extracted:
        raise ArchiveTooLargeError(
            f"Archive declares {total_declared} extracted bytes; "
            f"limit is {max_extracted}"
        )

    if total_declared > MIN_BOMB_SIZE_BYTES and total_compressed > 0:
        ratio = total_declared / total_compressed
        if ratio > settings.bomb_compression_ratio_limit:
            raise ArchiveBombError(
                f"Compression ratio {ratio:.0f}:1 exceeds limit "
                f"{settings.bomb_compression_ratio_limit}:1 — likely a bomb"
            )

    return ArchiveStats(
        file_count=file_count,
        total_compressed_bytes=total_compressed,
        total_declared_bytes=total_declared,
    )
