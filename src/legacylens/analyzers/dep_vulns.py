"""Dependency vulnerability analyzer.

Queries OSV.dev for every pinned dependency (only pinned — a floating
version has no single truth to check, and that gap is already reported by
DEP-UNPINNED-001). Batch-first for API politeness: one querybatch call
identifies affected packages, then details are fetched only for those.

  DEP-VULN-001          known vulnerabilities affect a pinned dependency
  EVID-UNAVAILABLE-001  OSV.dev unreachable; enrichment skipped
"""

import logging

from legacylens.analyzers.base import Analyzer, AnalyzerResult
from legacylens.analyzers.registry import registry
from legacylens.core.config import get_settings
from legacylens.core.exceptions import ExternalEvidenceError
from legacylens.domain.models import (
    Evidence,
    EvidenceSource,
    Finding,
    FindingCategory,
    ProjectContext,
    Severity,
)
from legacylens.evidence.osv import OsvClient, Vulnerability
from legacylens.parsing.manifests.models import (
    DeclaredDependency,
    DependencyInventory,
)

logger = logging.getLogger(__name__)

MAX_CITED_VULNS = 10

_LABEL_SEVERITY = {
    "CRITICAL": Severity.CRITICAL,
    "HIGH": Severity.HIGH,
    "MODERATE": Severity.MEDIUM,
    "MEDIUM": Severity.MEDIUM,
    "LOW": Severity.LOW,
}
_SEVERITY_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM,
                   Severity.LOW]


def _worst_severity(vulns: list[Vulnerability]) -> Severity:
    labels = {
        _LABEL_SEVERITY.get((v.severity_label or "").upper())
        for v in vulns
    }
    for severity in _SEVERITY_ORDER:
        if severity in labels:
            return severity
    return Severity.MEDIUM      # vulnerabilities exist, severity unpublished


@registry.register
class VulnerabilityAnalyzer(Analyzer):
    id = "dep_vulns"
    name = "Dependency vulnerability analyzer"
    depends_on = ("manifest_deps",)

    def __init__(self, client: OsvClient | None = None) -> None:
        self._client = client or OsvClient(get_settings())

    def analyze(self, ctx: ProjectContext) -> AnalyzerResult:
        inventory: DependencyInventory | None = ctx.get_artifact(
            "manifest_deps")
        if inventory is None:
            return AnalyzerResult()

        pinned = [d for d in inventory.dependencies
                  if d.is_pinned and d.version]
        if not pinned:
            return AnalyzerResult()

        try:
            id_lists = self._client.query_batch(
                [(d.name, d.ecosystem, d.version) for d in pinned]
            )
        except ExternalEvidenceError:
            return AnalyzerResult(findings=[self._unavailable(len(pinned))])

        findings: list[Finding] = []
        for dep, vuln_ids in zip(pinned, id_lists):
            if not vuln_ids:
                continue
            try:
                vulns = self._client.query(dep.name, dep.ecosystem,
                                           dep.version)
            except ExternalEvidenceError:
                findings.append(self._unavailable(1, dep.name))
                continue
            if vulns:
                findings.append(self._vuln_finding(dep, vulns))
        return AnalyzerResult(findings=findings)

    def _vuln_finding(
        self, dep: DeclaredDependency, vulns: list[Vulnerability]
    ) -> Finding:
        citations = [
            Evidence(
                source=EvidenceSource.EXTERNAL_AUTHORITY,
                reference_url=v.reference_url,
                detail=(
                    f"{v.id}"
                    + (f" ({', '.join(v.aliases)})" if v.aliases else "")
                    + (f" [{v.severity_label}]" if v.severity_label else "")
                    + (f": {v.summary}" if v.summary else "")
                ),
            )
            for v in vulns[:MAX_CITED_VULNS]
        ]
        manifest_evidence = Evidence(
            source=EvidenceSource.MANIFEST,
            file_path=dep.manifest_path,
            line_start=dep.line,
            snippet=dep.raw_spec,
        )
        return Finding(
            analyzer_id=self.id,
            rule_id="DEP-VULN-001",
            category=FindingCategory.SECURITY,
            severity=_worst_severity(vulns),
            title=(
                f"{dep.name} {dep.version} has {len(vulns)} known "
                f"vulnerabilit{'y' if len(vulns) == 1 else 'ies'}"
            ),
            description=(
                f"The pinned dependency {dep.name}=={dep.version} is "
                f"affected by {len(vulns)} published advisories in the OSV "
                "database. Upgrading this package is a migration "
                "prerequisite, not an optional cleanup — the advisories "
                "are cited below."
            ),
            evidence=[manifest_evidence, *citations],
            metadata={
                "package": dep.name,
                "version": dep.version,
                "ecosystem": str(dep.ecosystem),
                "vulnerability_ids": [v.id for v in vulns],
                "count": len(vulns),
            },
        )

    def _unavailable(self, package_count: int,
                     package: str | None = None) -> Finding:
        scope = (f"package '{package}'" if package
                 else f"{package_count} pinned dependencies")
        return Finding(
            analyzer_id=self.id,
            rule_id="EVID-UNAVAILABLE-001",
            category=FindingCategory.DOCUMENTATION,
            severity=Severity.INFO,
            title="Vulnerability enrichment unavailable for this run",
            description=(
                f"OSV.dev could not be reached; known-vulnerability status "
                f"was not verified for {scope}. Security findings in this "
                "report are a lower bound."
            ),
            evidence=[Evidence(
                source=EvidenceSource.EXTERNAL_AUTHORITY,
                detail=f"OSV.dev unreachable; scope: {scope}",
            )],
        )
