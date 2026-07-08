"""Manifest dependency analyzer.

The platform's first production analyzer. Walks the inventory, dispatches
each manifest to its ecosystem parser, publishes a DependencyInventory
artifact for downstream analyzers, and emits deterministic findings:

  MAN-PARSE-001   manifest exists but could not be parsed  (medium)
  DEP-UNPINNED-001 runtime dependencies with floating versions (medium)
  DEP-DUP-001     same package declared twice in one manifest (low)

Grouping policy: unpinned findings are grouped per manifest (one finding
with evidence per dependency line, capped) rather than one finding per
dependency — a 200-dependency package.json should not produce 200 findings
that drown the report.
"""

import logging
from collections import Counter

from legacylens.analyzers.base import Analyzer, AnalyzerResult
from legacylens.analyzers.registry import registry
from legacylens.domain.models import (
    Evidence,
    EvidenceSource,
    Finding,
    FindingCategory,
    ProjectContext,
    Severity,
)
from legacylens.parsing.manifests.base import ManifestParser
from legacylens.parsing.manifests.java_parsers import GradleParser, PomXmlParser
from legacylens.parsing.manifests.javascript_parsers import PackageJsonParser
from legacylens.parsing.manifests.models import (
    DeclaredDependency,
    DependencyInventory,
    DependencyScope,
)
from legacylens.parsing.manifests.python_parsers import (
    PyprojectTomlParser,
    RequirementsTxtParser,
)

logger = logging.getLogger(__name__)

MAX_EVIDENCE_PER_FINDING = 20
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


@registry.register
class ManifestDependencyAnalyzer(Analyzer):
    id = "manifest_deps"
    name = "Manifest dependency analyzer"

    PARSERS: tuple[ManifestParser, ...] = (
        RequirementsTxtParser(),
        PyprojectTomlParser(),
        PackageJsonParser(),
        PomXmlParser(),
        GradleParser(),
    )

    def analyze(self, ctx: ProjectContext) -> AnalyzerResult:
        inventory = DependencyInventory()
        findings: list[Finding] = []

        for record in ctx.files:
            if record.is_binary or record.size_bytes > MAX_MANIFEST_BYTES:
                continue
            parser = next(
                (p for p in self.PARSERS if p.matches(record.path)), None
            )
            if parser is None:
                continue

            text = (ctx.root / record.path).read_text(
                encoding="utf-8", errors="replace"
            )
            try:
                deps = parser.parse(text, record.path)
            except Exception as exc:  # noqa: BLE001 — one bad manifest
                findings.append(self._parse_failure(record.path, exc))
                continue

            inventory.dependencies.extend(deps)
            inventory.manifest_paths.append(record.path)
            findings.extend(self._manifest_rules(record.path, deps))

        return AnalyzerResult(findings=findings, artifact=inventory)

    # ------------------------------------------------------------------ rules

    def _parse_failure(self, path: str, exc: Exception) -> Finding:
        return Finding(
            analyzer_id=self.id,
            rule_id="MAN-PARSE-001",
            category=FindingCategory.CONFIGURATION,
            severity=Severity.MEDIUM,
            title=f"Unparseable manifest: {path}",
            description=(
                f"The manifest '{path}' exists but could not be parsed "
                f"({type(exc).__name__}: {exc}). Its dependencies are "
                "invisible to this analysis — migration planning for this "
                "component will be incomplete until the file is fixed."
            ),
            evidence=[Evidence(
                file_path=path,
                detail=f"{type(exc).__name__}: {exc}",
            )],
        )

    def _manifest_rules(
        self, path: str, deps: list[DeclaredDependency]
    ) -> list[Finding]:
        findings = []

        unpinned = [
            d for d in deps
            if d.is_pinned is False and d.scope == DependencyScope.RUNTIME
        ]
        if unpinned:
            findings.append(Finding(
                analyzer_id=self.id,
                rule_id="DEP-UNPINNED-001",
                category=FindingCategory.DEPENDENCY,
                severity=Severity.MEDIUM,
                title=(
                    f"{len(unpinned)} unpinned runtime "
                    f"dependencies in {path}"
                ),
                description=(
                    "Floating version specifiers mean builds are not "
                    "reproducible: the same commit can resolve different "
                    "dependency versions on different days. During a "
                    "migration this makes before/after comparison "
                    "unreliable. Pin exact versions (or add a lockfile) "
                    "before migration begins."
                ),
                evidence=[
                    Evidence(
                        source=EvidenceSource.MANIFEST,
                        file_path=d.manifest_path,
                        line_start=d.line or None,
                        snippet=d.raw_spec,
                    )
                    for d in unpinned[:MAX_EVIDENCE_PER_FINDING]
                ],
                metadata={
                    "count": len(unpinned),
                    "packages": [d.name for d in unpinned],
                },
            ))

        counts = Counter(d.name for d in deps)
        for name, count in counts.items():
            if count < 2:
                continue
            dupes = [d for d in deps if d.name == name]
            findings.append(Finding(
                analyzer_id=self.id,
                rule_id="DEP-DUP-001",
                category=FindingCategory.DEPENDENCY,
                severity=Severity.LOW,
                title=f"'{name}' declared {count} times in {path}",
                description=(
                    f"The package '{name}' appears {count} times in "
                    f"'{path}'. Resolution order decides which wins, which "
                    "is fragile and usually indicates merge debt."
                ),
                evidence=[
                    Evidence(
                        source=EvidenceSource.MANIFEST,
                        file_path=d.manifest_path,
                        line_start=d.line or None,
                        snippet=d.raw_spec,
                    )
                    for d in dupes[:MAX_EVIDENCE_PER_FINDING]
                ],
            ))

        return findings
