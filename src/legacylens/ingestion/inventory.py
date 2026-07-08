"""File inventory.

Walks the extracted project once and produces the FileRecord list that
every analyzer downstream consumes. Each file is read exactly once: the
first 8 KiB feeds binary sniffing and shebang detection while the full
stream feeds the SHA-256 hash (dedup, caching, and audit trails later).

Vendor and tooling directories are excluded — node_modules alone would
otherwise dominate every metric the platform reports.
"""

import hashlib
import logging
from pathlib import Path

from legacylens.core.logging import log_with_fields
from legacylens.domain.models import FileRecord
from legacylens.ingestion.languages import detect_language

logger = logging.getLogger(__name__)

DEFAULT_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        "node_modules",
        "bower_components",
        "venv",
        ".venv",
        "env",
        "site-packages",
        "target",       # maven / rust build output
        "build",
        "dist",
        "out",
        "bin",
        "obj",
        ".gradle",
        "coverage",
        ".next",
        ".terraform",
        "vendor",
    }
)

_SNIFF_BYTES = 8192
_HASH_CHUNK = 1024 * 1024


def _read_head_and_hash(path: Path) -> tuple[bytes, str]:
    digest = hashlib.sha256()
    head = b""
    with open(path, "rb") as fh:
        first = fh.read(_SNIFF_BYTES)
        head = first
        digest.update(first)
        while chunk := fh.read(_HASH_CHUNK):
            digest.update(chunk)
    return head, digest.hexdigest()


def build_inventory(
    root: Path,
    ignored_dirs: frozenset[str] = DEFAULT_IGNORED_DIRS,
) -> list[FileRecord]:
    """Produce sorted FileRecords for every non-ignored file under root."""
    records: list[FileRecord] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root)
        if any(part in ignored_dirs for part in rel.parts[:-1]):
            continue

        head, sha256 = _read_head_and_hash(path)
        is_binary = b"\x00" in head
        language = None if is_binary else detect_language(rel.as_posix(), head)

        records.append(
            FileRecord(
                path=rel.as_posix(),
                size_bytes=path.stat().st_size,
                sha256=sha256,
                language=language,
                is_binary=is_binary,
            )
        )

    log_with_fields(
        logger, logging.INFO, "inventory built",
        files=len(records),
        binary_files=sum(1 for r in records if r.is_binary),
    )
    return records
