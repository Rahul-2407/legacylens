"""Shared helpers for analyzers."""

from legacylens.domain.models import FileRecord, ProjectContext

DEFAULT_MAX_BYTES = 512 * 1024


def read_text(
    ctx: ProjectContext,
    record: FileRecord,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> str | None:
    """Read a file's text, or None if binary/oversized/unreadable."""
    if record.is_binary or record.size_bytes > max_bytes:
        return None
    try:
        return (ctx.root / record.path).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return None
