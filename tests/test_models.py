"""Domain model tests — the evidence invariant is the headline."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from legacylens.domain.models import (
    Evidence,
    EvidenceSource,
    FileRecord,
    Finding,
    FindingCategory,
    ProjectContext,
    Severity,
)


def make_evidence(**overrides) -> Evidence:
    data = {
        "file_path": "src/app/legacy.py",
        "line_start": 10,
        "line_end": 12,
        "snippet": "import imp",
    }
    data.update(overrides)
    return Evidence(**data)


class TestFindingInvariants:
    def test_finding_without_evidence_is_impossible(self):
        """The core platform principle: no evidence, no finding."""
        with pytest.raises(ValidationError):
            Finding(
                analyzer_id="tech_detection",
                rule_id="TECH-001",
                category=FindingCategory.TECHNOLOGY,
                severity=Severity.LOW,
                title="Deprecated import",
                description="Uses the removed 'imp' module.",
                evidence=[],
            )

    def test_valid_finding_gets_generated_id(self):
        finding = Finding(
            analyzer_id="tech_detection",
            rule_id="TECH-001",
            category=FindingCategory.TECHNOLOGY,
            severity=Severity.MEDIUM,
            title="Deprecated import",
            description="Uses the removed 'imp' module.",
            evidence=[make_evidence()],
        )
        assert finding.finding_id.startswith("F-")
        assert len(finding.evidence) == 1

    def test_two_findings_get_distinct_ids(self):
        kwargs = dict(
            analyzer_id="a",
            rule_id="R-1",
            category=FindingCategory.DEPENDENCY,
            severity=Severity.HIGH,
            title="t",
            description="d",
            evidence=[make_evidence()],
        )
        assert Finding(**kwargs).finding_id != Finding(**kwargs).finding_id


class TestEvidence:
    def test_line_range_must_be_ordered(self):
        with pytest.raises(ValidationError):
            make_evidence(line_start=20, line_end=10)

    def test_external_authority_evidence(self):
        ev = Evidence(
            source=EvidenceSource.EXTERNAL_AUTHORITY,
            reference_url="https://endoflife.date/spring-framework",
            detail="Spring 4.3 reached end of life on 2020-12-31",
        )
        assert ev.file_path is None
        assert ev.source == EvidenceSource.EXTERNAL_AUTHORITY

    def test_evidence_is_immutable(self):
        ev = make_evidence()
        with pytest.raises(ValidationError):
            ev.file_path = "elsewhere.py"


class TestProjectContext:
    def test_artifact_access(self):
        ctx = ProjectContext(project_id="p1", root=Path("/tmp/p1"))
        assert ctx.get_artifact("missing") is None
        ctx.artifacts["manifest_parser"] = {"deps": ["flask==0.12"]}
        assert ctx.get_artifact("manifest_parser") == {"deps": ["flask==0.12"]}

    def test_files_by_language(self):
        ctx = ProjectContext(
            project_id="p1",
            root=Path("/tmp/p1"),
            files=[
                FileRecord(path="a.py", size_bytes=10, language="python"),
                FileRecord(path="b.java", size_bytes=20, language="java"),
                FileRecord(path="c.py", size_bytes=5, language="python"),
            ],
        )
        assert [f.path for f in ctx.files_by_language("python")] == ["a.py", "c.py"]
