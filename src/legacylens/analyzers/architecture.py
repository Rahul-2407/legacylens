"""Architecture analyzer.

Consumes the ModuleGraph, runs the graph algorithms, publishes the
ArchitectureReport artifact (the migration planner's raw material), and
emits:

  ARCH-CYCLE-001    a cyclic dependency group (HIGH) — cycles cannot be
                    migrated incrementally; the whole group moves together
                    or the cycle is broken first
  ARCH-HOTSPOT-001  high fan-in modules (MEDIUM) — coupling bottlenecks
                    whose change ripples widest
  ARCH-WAVES-001    migration wave structure summary (INFO)
"""

import logging

from legacylens.analyzers.base import Analyzer, AnalyzerResult
from legacylens.analyzers.registry import registry
from legacylens.core.logging import log_with_fields
from legacylens.domain.models import (
    Evidence,
    Finding,
    FindingCategory,
    ProjectContext,
    Severity,
)
from legacylens.graph.algorithms import analyze_architecture
from legacylens.parsing.ast.module_graph import ModuleGraph

logger = logging.getLogger(__name__)

HOTSPOT_FAN_IN_THRESHOLD = 10
MAX_CYCLE_FINDINGS = 10
MAX_EVIDENCE_PER_FINDING = 20


@registry.register
class ArchitectureAnalyzer(Analyzer):
    id = "architecture"
    name = "Architecture analyzer"
    depends_on = ("module_graph",)

    def analyze(self, ctx: ProjectContext) -> AnalyzerResult:
        graph: ModuleGraph | None = ctx.get_artifact("module_graph")
        if graph is None or not graph.files:
            return AnalyzerResult()

        report = analyze_architecture(graph)
        findings: list[Finding] = []
        findings += self._cycle_findings(graph, report.cycles)
        findings += self._hotspot_findings(report)
        findings.append(self._waves_summary(report))

        log_with_fields(
            logger, logging.INFO, "architecture analyzed",
            modules=len(report.metrics),
            cycles=len(report.cycles),
            waves=len(report.waves),
        )
        return AnalyzerResult(findings=findings, artifact=report)

    def _cycle_findings(
        self, graph: ModuleGraph, cycles: list[list[str]]
    ) -> list[Finding]:
        findings = []
        for cycle in cycles[:MAX_CYCLE_FINDINGS]:
            members = set(cycle)
            edges_in_cycle = [
                e for e in graph.internal_edges
                if e.source in members and e.target in members
            ]
            findings.append(Finding(
                analyzer_id=self.id,
                rule_id="ARCH-CYCLE-001",
                category=FindingCategory.ARCHITECTURE,
                severity=Severity.HIGH,
                title=(
                    f"Cyclic dependency between {len(cycle)} modules"
                ),
                description=(
                    f"These {len(cycle)} modules import each other in a "
                    "cycle, so none can be migrated, tested, or deployed "
                    "independently — the group is one indivisible "
                    "migration unit. Either plan them as a single phase "
                    "or break the cycle first (typically by extracting "
                    "the shared interface both sides depend on). Members: "
                    + ", ".join(cycle[:8])
                    + ("…" if len(cycle) > 8 else "")
                ),
                evidence=[
                    Evidence(
                        file_path=e.source,
                        line_start=e.line,
                        snippet=e.raw,
                        detail=f"imports {e.target}",
                    )
                    for e in edges_in_cycle[:MAX_EVIDENCE_PER_FINDING]
                ],
                metadata={"members": cycle,
                          "edge_count": len(edges_in_cycle)},
            ))
        return findings

    def _hotspot_findings(self, report) -> list[Finding]:
        hotspots = sorted(
            (m for m in report.metrics.values()
             if m.fan_in >= HOTSPOT_FAN_IN_THRESHOLD),
            key=lambda m: -m.fan_in,
        )
        if not hotspots:
            return []
        return [Finding(
            analyzer_id=self.id,
            rule_id="ARCH-HOTSPOT-001",
            category=FindingCategory.ARCHITECTURE,
            severity=Severity.MEDIUM,
            title=(
                f"{len(hotspots)} high-coupling modules "
                f"(fan-in ≥ {HOTSPOT_FAN_IN_THRESHOLD})"
            ),
            description=(
                "These modules are imported by many others; any change to "
                "them ripples across the codebase. During migration they "
                "are the riskiest components to touch and the strongest "
                "candidates for a stable compatibility interface (or an "
                "anti-corruption layer) so dependents can migrate "
                "independently."
            ),
            evidence=[
                Evidence(
                    file_path=m.path,
                    detail=f"fan-in {m.fan_in}, fan-out {m.fan_out}",
                )
                for m in hotspots[:MAX_EVIDENCE_PER_FINDING]
            ],
            metadata={"hotspots": [
                {"path": m.path, "fan_in": m.fan_in, "fan_out": m.fan_out}
                for m in hotspots
            ]},
        )]

    def _waves_summary(self, report) -> Finding:
        sizes = report.parallelizable_wave_sizes
        return Finding(
            analyzer_id=self.id,
            rule_id="ARCH-WAVES-001",
            category=FindingCategory.ARCHITECTURE,
            severity=Severity.INFO,
            title=(
                f"Migration order computed: {len(sizes)} waves "
                f"across {sum(sizes)} modules"
            ),
            description=(
                "Modules were topologically ordered by dependency: wave 0 "
                "has no internal dependencies and migrates first; each "
                "later wave depends only on earlier ones. Modules within "
                "a wave are independent of each other and can be migrated "
                f"in parallel. Wave sizes: {sizes}. Cyclic groups appear "
                "as single units in their wave."
            ),
            evidence=[Evidence(
                detail=f"waves={len(sizes)}, sizes={sizes}",
            )],
            metadata={"wave_sizes": sizes},
        )
