"""Safe zip extraction.

Never calls ZipFile.extractall(). Each member is streamed manually to a
path we computed ourselves from a normalized, validated member name, and a
running counter enforces the extracted-size ceiling on bytes ACTUALLY
written — the defense that still holds when zip headers lie about
uncompressed sizes.
"""

import logging
import shutil
import zipfile
from pathlib import Path

from legacylens.core.config import Settings
from legacylens.core.exceptions import ArchiveBombError
from legacylens.core.logging import log_with_fields
from legacylens.ingestion.safety import inspect_zip, normalize_member_path

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 1024  # 1 MiB


class _CappedWriter:
    """Wraps copy loops with a shared running byte budget."""

    def __init__(self, limit_bytes: int) -> None:
        self.limit = limit_bytes
        self.written = 0

    def copy(self, src, dst) -> None:
        while True:
            chunk = src.read(_CHUNK)
            if not chunk:
                return
            self.written += len(chunk)
            if self.written > self.limit:
                raise ArchiveBombError(
                    "Extraction exceeded the configured size ceiling; "
                    "archive headers under-declared content (bomb suspected)"
                )
            dst.write(chunk)


def extract_zip(archive_path: Path, dest: Path, settings: Settings) -> int:
    """Inspect, then safely extract the archive into `dest`.

    Returns the number of files extracted. Cleans up `dest` on failure so a
    half-extracted bomb never lingers in the workspace.
    """
    stats = inspect_zip(archive_path, settings)
    dest.mkdir(parents=True, exist_ok=True)
    writer = _CappedWriter(settings.max_extracted_size_mb * 1024 * 1024)
    extracted = 0

    try:
        with zipfile.ZipFile(archive_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                member_path = normalize_member_path(info.filename)
                target = dest / member_path
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    writer.copy(src, out)
                extracted += 1
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    log_with_fields(
        logger, logging.INFO, "archive extracted",
        files=extracted,
        declared_bytes=stats.total_declared_bytes,
        written_bytes=writer.written,
    )
    return extracted
