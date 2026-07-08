"""Synchronous pipeline runner.

Executes analyzers in registry-resolved order against one ProjectContext.
Design decisions that matter:

* Partial-failure tolerance: one analyzer crashing is recorded and skipped;
  the rest of the pipeline continues. A 60%-complete analysis of a legacy
  system is still valuable — an all-or-nothing pipeline is not.
* Skip propagation: if an analyzer failed, analyzers that depend on it are
  skipped (not run against missing artifacts) and recorded as skipped.
* Invariant enforcement: the runner stamps/verifies analyzer_id on every
  finding, so provenance can never be spoofed or forgotten.

In Module 12 this runner is wrapped in a Celery task; the logic here does
not change — that separation is deliberate.
"""

import logging
import time

from pydantic import BaseModel, Field

from legacylens.analyzers.base import Analyzer
from legacylens.analyzers.registry import AnalyzerRegistry
from legacylens.core.logging import log_with_fields, set_correlation_id
from legacylens.domain.models import Finding, ProjectContext

logger = logging.getLogger(__name__)


class AnalyzerRunReport(BaseModel):
    analyzer_id: str
    status: str  # "succeeded" | "failed" | "skipped"
    duration_seconds: float = 0.0
    finding_count: int = 0
    error: str | None = None
    skipped_because: str | None = None


class PipelineResult(BaseModel):
    project_id: str
    findings: list[Finding] = Field(default_factory=list)
    reports: list[AnalyzerRunReport] = Field(default_factory=list)

    @property
    def succeeded_ids(self) -> list[str]:
        return [r.analyzer_id for r in self.reports if r.status == "succeeded"]

    @property
    def failed_ids(self) -> list[str]:
        return [r.analyzer_id for r in self.reports if r.status == "failed"]


class PipelineRunner:
    def __init__(self, registry: AnalyzerRegistry) -> None:
        self._registry = registry

    def run(
        self,
        ctx: ProjectContext,
        analyzer_ids: list[str] | None = None,
    ) -> PipelineResult:
        set_correlation_id(ctx.project_id)
        ordered = self._registry.resolve_order(analyzer_ids)
        result = PipelineResult(project_id=ctx.project_id)
        unavailable: dict[str, str] = {}  # analyzer_id -> reason

        log_with_fields(
            logger, logging.INFO, "pipeline started",
            analyzer_count=len(ordered),
            order=[cls.id for cls in ordered],
        )

        for analyzer_cls in ordered:
            report = self._run_one(analyzer_cls, ctx, unavailable)
            result.reports.append(report)
            if report.status == "succeeded":
                run_findings = ctx.artifacts.pop(
                    f"__findings__{analyzer_cls.id}", []
                )
                result.findings.extend(run_findings)
            else:
                unavailable[analyzer_cls.id] = report.status

        log_with_fields(
            logger, logging.INFO, "pipeline finished",
            findings=len(result.findings),
            succeeded=len(result.succeeded_ids),
            failed=len(result.failed_ids),
        )
        set_correlation_id(None)
        return result

    def _run_one(
        self,
        analyzer_cls: type[Analyzer],
        ctx: ProjectContext,
        unavailable: dict[str, str],
    ) -> AnalyzerRunReport:
        blocked = [d for d in analyzer_cls.depends_on if d in unavailable]
        if blocked:
            reason = f"upstream unavailable: {', '.join(sorted(blocked))}"
            log_with_fields(
                logger, logging.WARNING, "analyzer skipped",
                analyzer=analyzer_cls.id, reason=reason,
            )
            return AnalyzerRunReport(
                analyzer_id=analyzer_cls.id,
                status="skipped",
                skipped_because=reason,
            )

        started = time.perf_counter()
        try:
            output = analyzer_cls().analyze(ctx)
        except Exception as exc:  # noqa: BLE001 — isolation is the point
            duration = time.perf_counter() - started
            logger.exception("analyzer failed: %s", analyzer_cls.id)
            return AnalyzerRunReport(
                analyzer_id=analyzer_cls.id,
                status="failed",
                duration_seconds=round(duration, 4),
                error=f"{type(exc).__name__}: {exc}",
            )

        duration = time.perf_counter() - started
        findings = [
            f.model_copy(update={"analyzer_id": analyzer_cls.id})
            for f in output.findings
        ]
        if output.artifact is not None:
            ctx.artifacts[analyzer_cls.id] = output.artifact
        # Stash findings for the run() loop to collect; keyed so analyzers
        # cannot accidentally read each other's raw findings as artifacts.
        ctx.artifacts[f"__findings__{analyzer_cls.id}"] = findings

        log_with_fields(
            logger, logging.INFO, "analyzer succeeded",
            analyzer=analyzer_cls.id,
            findings=len(findings),
            duration_seconds=round(duration, 4),
        )
        return AnalyzerRunReport(
            analyzer_id=analyzer_cls.id,
            status="succeeded",
            duration_seconds=round(duration, 4),
            finding_count=len(findings),
        )
