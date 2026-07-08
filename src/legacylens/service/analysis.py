"""Analysis orchestration — the plain function behind every entry point.

The Celery task, the API's synchronous test mode, and any future CLI all
call this one function. Every dependency is a parameter (session factory,
settings, LLM client), which is why the whole service layer is testable
with tmp SQLite and a scripted LLM — Celery and Redis are deployment
concerns, not logic concerns.

Failure policy mirrors the rest of the platform: the job never leaves a
project in 'running'. Any exception marks it failed with the recorded
reason. LLM unavailability is NOT a failure — synthesis degrades and the
deterministic report ships.
"""

import logging
from pathlib import Path

from legacylens.agents.graph import SynthesisResult
from legacylens.analyzers.builtin import load_builtin_analyzers
from legacylens.analyzers.registry import registry
from legacylens.artifacts.report import ReportBuilder
from legacylens.core.config import Settings, get_settings
from legacylens.core.logging import log_with_fields
from legacylens.db import session as repo
from legacylens.ingestion.ingestor import ProjectIngestor
from legacylens.pipeline.runner import PipelineRunner
from legacylens.scoring.engine import compute_scorecard

logger = logging.getLogger(__name__)


def run_analysis(
    project_id: str,
    archive_path: str,
    session_factory,
    settings: Settings | None = None,
    llm=None,
) -> None:
    settings = settings or get_settings()
    load_builtin_analyzers()

    with session_factory() as session:
        repo.set_status(session, project_id, "running")

    try:
        ctx = ProjectIngestor(settings).ingest_archive(
            Path(archive_path), project_id=project_id)
        result = PipelineRunner(registry).run(ctx)
        scorecard = compute_scorecard(result.findings, ctx)
        synthesis = _try_synthesis(result.findings, scorecard, ctx,
                                   settings, llm)
        report_md = ReportBuilder().build_markdown(
            ctx, result.findings, scorecard, synthesis)
        diagrams = _diagrams(ctx)

        with session_factory() as session:
            repo.save_findings(session, project_id, result.findings)
            repo.save_artifact(session, project_id, "report_md", report_md)
            repo.save_artifact(session, project_id, "scorecard",
                               scorecard.model_dump_json())
            if synthesis is not None:
                repo.save_artifact(session, project_id, "synthesis",
                                   synthesis.model_dump_json())
            for kind, payload in diagrams.items():
                repo.save_artifact(session, project_id, kind, payload)
            repo.set_status(
                session, project_id, "completed",
                file_count=len(ctx.files),
                readiness=scorecard.readiness.value,
            )
        log_with_fields(
            logger, logging.INFO, "analysis completed",
            project_id=project_id,
            findings=len(result.findings),
            readiness=scorecard.readiness.value,
        )
    except Exception as exc:  # noqa: BLE001 — job must never stay 'running'
        logger.exception("analysis failed: %s", project_id)
        with session_factory() as session:
            repo.set_status(session, project_id, "failed",
                            error=f"{type(exc).__name__}: {exc}")


def _try_synthesis(findings, scorecard, ctx, settings,
                   llm) -> SynthesisResult | None:
    if llm is None and not settings.groq_api_key:
        return None                      # no LLM configured: not an error
    try:
        from legacylens.agents.graph import SynthesisEngine
        return SynthesisEngine(llm=llm).run(findings, scorecard, ctx)
    except Exception:  # noqa: BLE001 — synthesis is optional by design
        logger.exception("synthesis unavailable; deterministic report only")
        return None


def _diagrams(ctx) -> dict[str, str]:
    """Render Mermaid once at analysis time; the dashboard serves it as-is."""
    from legacylens.artifacts.mermaid import (
        module_graph_mermaid, waves_mermaid,
    )
    diagrams: dict[str, str] = {}
    graph = ctx.get_artifact("module_graph")
    arch = ctx.get_artifact("architecture")
    if graph and graph.internal_edges:
        diagrams["mermaid_modules"] = module_graph_mermaid(graph, arch)
    if arch and arch.waves:
        diagrams["mermaid_waves"] = waves_mermaid(arch)
    return diagrams
