"""Project ingestion facade.

The single entry point the rest of the platform uses:

    ctx = ProjectIngestor().ingest_archive(Path("upload.zip"))
    result = PipelineRunner(registry).run(ctx)

Two sources are supported: a zip archive (production upload path, fully
hardened) and an existing directory (developer path — point it at a cloned
repository). Settings are injected rather than read from the global
singleton inside methods, so tests and future multi-tenant configs can
supply their own limits.
"""

import logging
import uuid
from pathlib import Path

from legacylens.core.config import Settings, get_settings
from legacylens.core.exceptions import IngestionError
from legacylens.core.logging import log_with_fields, set_correlation_id
from legacylens.domain.models import ProjectContext
from legacylens.ingestion.extractor import extract_zip
from legacylens.ingestion.inventory import DEFAULT_IGNORED_DIRS, build_inventory

logger = logging.getLogger(__name__)


def _new_project_id() -> str:
    return f"proj-{uuid.uuid4().hex[:12]}"


class ProjectIngestor:
    def __init__(
        self,
        settings: Settings | None = None,
        ignored_dirs: frozenset[str] = DEFAULT_IGNORED_DIRS,
    ) -> None:
        self._settings = settings or get_settings()
        self._ignored_dirs = ignored_dirs

    def ingest_archive(
        self, archive_path: Path, project_id: str | None = None
    ) -> ProjectContext:
        """Validate, extract, and inventory an uploaded zip archive."""
        if not archive_path.is_file():
            raise IngestionError(f"Archive not found: {archive_path}")
        project_id = project_id or _new_project_id()
        set_correlation_id(project_id)

        source_root = (
            self._settings.workspace_dir.expanduser().resolve()
            / project_id
            / "source"
        )
        extracted = extract_zip(archive_path, source_root, self._settings)
        ctx = self._build_context(project_id, source_root)

        log_with_fields(
            logger, logging.INFO, "project ingested from archive",
            archive=archive_path.name,
            extracted_files=extracted,
            inventoried_files=len(ctx.files),
        )
        return ctx

    def ingest_directory(
        self, source: Path, project_id: str | None = None
    ) -> ProjectContext:
        """Ingest an existing directory in place (e.g. a cloned repository)."""
        source = source.expanduser().resolve()
        if not source.is_dir():
            raise IngestionError(f"Directory not found: {source}")
        project_id = project_id or _new_project_id()
        set_correlation_id(project_id)

        ctx = self._build_context(project_id, source)
        log_with_fields(
            logger, logging.INFO, "project ingested from directory",
            source=str(source),
            inventoried_files=len(ctx.files),
        )
        return ctx

    def _build_context(self, project_id: str, root: Path) -> ProjectContext:
        files = build_inventory(root, self._ignored_dirs)
        if not files:
            raise IngestionError(
                "Project contains no analyzable files after ingestion"
            )
        return ProjectContext(project_id=project_id, root=root, files=files)
