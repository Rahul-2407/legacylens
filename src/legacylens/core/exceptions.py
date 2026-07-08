"""Exception hierarchy for LegacyLens.

Every custom exception derives from LegacyLensError so callers can catch
platform errors distinctly from programming errors.
"""


class LegacyLensError(Exception):
    """Base class for all LegacyLens errors."""


class RegistryError(LegacyLensError):
    """Base class for analyzer-registry errors."""


class DuplicateAnalyzerError(RegistryError):
    """Two analyzers were registered with the same id."""

    def __init__(self, analyzer_id: str) -> None:
        super().__init__(f"Analyzer id '{analyzer_id}' is already registered")
        self.analyzer_id = analyzer_id


class UnknownAnalyzerError(RegistryError):
    """An analyzer id was requested or depended upon but never registered."""

    def __init__(self, analyzer_id: str, required_by: str | None = None) -> None:
        detail = f"Unknown analyzer id '{analyzer_id}'"
        if required_by:
            detail += f" (required by '{required_by}')"
        super().__init__(detail)
        self.analyzer_id = analyzer_id
        self.required_by = required_by


class CyclicDependencyError(RegistryError):
    """Analyzer depends_on declarations form a cycle."""


class InvalidFindingError(LegacyLensError):
    """An analyzer emitted a finding that violates platform invariants."""


class IngestionError(LegacyLensError):
    """Base class for project ingestion errors."""


class UnsafeArchiveError(IngestionError):
    """Archive contains path traversal, absolute paths, or symlink members."""


class ArchiveTooLargeError(IngestionError):
    """Archive exceeds size, extracted-size, or file-count ceilings."""


class ArchiveBombError(IngestionError):
    """Archive looks like a decompression bomb (ratio or streaming cap)."""


class ExternalEvidenceError(LegacyLensError):
    """An external evidence API (EOL, OSV) could not be reached or parsed.

    Analyzers catch this and degrade honestly: analysis continues, the
    report states which enrichment was unavailable."""


class LlmError(LegacyLensError):
    """The LLM provider could not be reached or returned an unusable
    response. Synthesis degrades gracefully; deterministic findings and
    scores are unaffected."""
