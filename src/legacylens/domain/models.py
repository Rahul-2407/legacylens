"""Domain models — the shared language of LegacyLens.

The single most important invariant of the platform lives here:

    A Finding cannot exist without at least one Evidence record.

Every recommendation the platform ever makes traces back to Findings, and
every Finding traces back to concrete evidence (a file and line, or an
external authority such as endoflife.date). This is what makes the system
an evidence-grounded architect rather than a text generator.
"""

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingCategory(StrEnum):
    TECHNOLOGY = "technology"
    DEPENDENCY = "dependency"
    ARCHITECTURE = "architecture"
    DATABASE = "database"
    API = "api"
    CONFIGURATION = "configuration"
    TECHNICAL_DEBT = "technical_debt"
    SECURITY = "security"
    PERFORMANCE = "performance"
    TESTING = "testing"
    DOCUMENTATION = "documentation"


class EvidenceSource(StrEnum):
    STATIC_ANALYSIS = "static_analysis"   # derived from parsing project files
    MANIFEST = "manifest"                 # package/build manifests
    EXTERNAL_AUTHORITY = "external"       # endoflife.date, OSV.dev, deps.dev
    HEURISTIC = "heuristic"               # computed metric with stated formula


class Evidence(BaseModel):
    """A verifiable pointer to where a fact was observed."""

    model_config = ConfigDict(frozen=True)

    source: EvidenceSource = EvidenceSource.STATIC_ANALYSIS
    file_path: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    snippet: str | None = Field(default=None, max_length=2000)
    reference_url: str | None = None  # for external authorities
    detail: str | None = None

    @field_validator("line_end")
    @classmethod
    def _line_range_is_ordered(cls, v: int | None, info) -> int | None:
        start = info.data.get("line_start")
        if v is not None and start is not None and v < start:
            raise ValueError("line_end must be >= line_start")
        return v


def _new_finding_id() -> str:
    return f"F-{uuid.uuid4().hex[:12]}"


class Finding(BaseModel):
    """A single fact discovered about the project, with mandatory evidence."""

    finding_id: str = Field(default_factory=_new_finding_id)
    analyzer_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)          # e.g. "DEP-EOL-001"
    category: FindingCategory
    severity: Severity
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)  # the invariant
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class FileRecord(BaseModel):
    """One file in the analyzed project, as inventoried during ingestion."""

    model_config = ConfigDict(frozen=True)

    path: str                       # relative to project root, POSIX-style
    size_bytes: int = Field(ge=0)
    sha256: str | None = None
    language: str | None = None     # filled by language detection (Module 2)
    is_binary: bool = False


class ProjectContext(BaseModel):
    """Shared state passed to every analyzer during one pipeline run.

    Analyzers read `files` and upstream `artifacts`; they never mutate each
    other's outputs. The runner is the only writer of `artifacts`, keyed by
    the producing analyzer's id — a deliberate blackboard pattern that keeps
    analyzers decoupled while letting them build on each other's work.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    project_id: str
    root: Path
    files: list[FileRecord] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)

    def get_artifact(self, analyzer_id: str) -> Any:
        """Return the artifact produced by an upstream analyzer, or None."""
        return self.artifacts.get(analyzer_id)

    def files_by_language(self, language: str) -> list[FileRecord]:
        return [f for f in self.files if f.language == language]
