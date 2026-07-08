"""Pipeline runner tests: the behaviors that make it production-grade."""

from pathlib import Path

import pytest

from legacylens.analyzers.base import Analyzer, AnalyzerResult
from legacylens.analyzers.registry import AnalyzerRegistry
from legacylens.domain.models import (
    Evidence,
    Finding,
    FindingCategory,
    ProjectContext,
    Severity,
)
from legacylens.pipeline.runner import PipelineRunner


def make_finding(rule_id: str = "R-1") -> Finding:
    return Finding(
        analyzer_id="placeholder",  # runner must overwrite with true producer
        rule_id=rule_id,
        category=FindingCategory.DEPENDENCY,
        severity=Severity.MEDIUM,
        title="t",
        description="d",
        evidence=[Evidence(file_path="requirements.txt", line_start=1)],
    )


@pytest.fixture()
def ctx() -> ProjectContext:
    return ProjectContext(project_id="proj-123", root=Path("/tmp/proj-123"))


def test_artifacts_flow_downstream_and_findings_collected(ctx):
    reg = AnalyzerRegistry()

    @reg.register
    class Producer(Analyzer):
        id = "producer"
        name = "Producer"

        def analyze(self, context):
            return AnalyzerResult(
                findings=[make_finding("PROD-1")],
                artifact={"dependencies": ["flask==0.12"]},
            )

    @reg.register
    class Consumer(Analyzer):
        id = "consumer"
        name = "Consumer"
        depends_on = ("producer",)

        def analyze(self, context):
            deps = context.get_artifact("producer")["dependencies"]
            assert deps == ["flask==0.12"]
            return AnalyzerResult(findings=[make_finding("CONS-1")])

    result = PipelineRunner(reg).run(ctx)

    assert result.succeeded_ids == ["producer", "consumer"]
    assert sorted(f.rule_id for f in result.findings) == ["CONS-1", "PROD-1"]


def test_provenance_is_stamped_by_runner(ctx):
    reg = AnalyzerRegistry()

    @reg.register
    class Sneaky(Analyzer):
        id = "sneaky"
        name = "Sneaky"

        def analyze(self, context):
            return AnalyzerResult(findings=[make_finding()])  # claims "placeholder"

    result = PipelineRunner(reg).run(ctx)
    assert result.findings[0].analyzer_id == "sneaky"


def test_failure_is_isolated_and_dependents_are_skipped(ctx):
    reg = AnalyzerRegistry()

    @reg.register
    class Broken(Analyzer):
        id = "broken"
        name = "Broken"

        def analyze(self, context):
            raise RuntimeError("parser exploded")

    @reg.register
    class Dependent(Analyzer):
        id = "dependent"
        name = "Dependent"
        depends_on = ("broken",)

        def analyze(self, context):  # pragma: no cover — must not run
            raise AssertionError("should have been skipped")

    @reg.register
    class Independent(Analyzer):
        id = "independent"
        name = "Independent"

        def analyze(self, context):
            return AnalyzerResult(findings=[make_finding("IND-1")])

    result = PipelineRunner(reg).run(ctx)
    by_id = {r.analyzer_id: r for r in result.reports}

    assert by_id["broken"].status == "failed"
    assert "parser exploded" in by_id["broken"].error
    assert by_id["dependent"].status == "skipped"
    assert "broken" in by_id["dependent"].skipped_because
    assert by_id["independent"].status == "succeeded"
    assert [f.rule_id for f in result.findings] == ["IND-1"]


def test_internal_finding_stash_does_not_leak_into_artifacts(ctx):
    reg = AnalyzerRegistry()

    @reg.register
    class Solo(Analyzer):
        id = "solo"
        name = "Solo"

        def analyze(self, context):
            return AnalyzerResult(findings=[make_finding()])

    PipelineRunner(reg).run(ctx)
    assert all(not key.startswith("__findings__") for key in ctx.artifacts)
