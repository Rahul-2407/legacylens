"""LegacyLens — AI Software Migration Platform.

Core principle: deterministic analysis produces facts (Findings backed by
Evidence); AI produces judgment grounded in those facts. A Finding without
evidence cannot be constructed — the principle is enforced by the type system.
"""

from legacylens.domain.models import (
    Evidence,
    FileRecord,
    Finding,
    FindingCategory,
    ProjectContext,
    Severity,
)
from legacylens.analyzers.base import Analyzer, AnalyzerResult
from legacylens.analyzers.registry import AnalyzerRegistry, registry
from legacylens.pipeline.runner import PipelineResult, PipelineRunner

__version__ = "0.1.0"

__all__ = [
    "Analyzer",
    "AnalyzerRegistry",
    "AnalyzerResult",
    "Evidence",
    "FileRecord",
    "Finding",
    "FindingCategory",
    "PipelineResult",
    "PipelineRunner",
    "ProjectContext",
    "Severity",
    "registry",
]
