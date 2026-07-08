"""Technical debt analyzer.

Four signals, each chosen for migration relevance rather than style:

  DEBT-TESTS-001  little or no automated tests (HIGH) — the migration
                  safety net does not exist; behavior cannot be verified
                  before/after. The single biggest migration risk there is.
  DEBT-TODO-001   TODO/FIXME/HACK density (LOW) — self-declared debt
  DEBT-DEAD-001   files no other module imports (MEDIUM) — delete-before-
                  migrate candidates (why pay to migrate dead code?)
  DEBT-LARGE-001  god files over the line threshold (MEDIUM) — cannot be
                  migrated incrementally without splitting first
"""

import re
from collections import Counter
from pathlib import PurePosixPath

from legacylens.analyzers.base import Analyzer, AnalyzerResult
from legacylens.analyzers.registry import registry
from legacylens.analyzers.util import read_text
from legacylens.domain.models import (
    Evidence,
    EvidenceSource,
    Finding,
    FindingCategory,
    ProjectContext,
    Severity,
)
from legacylens.parsing.ast.module_graph import ModuleGraph

_SOURCE_LANGUAGES = {"python", "java", "javascript", "typescript"}
_MARKER = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
_TEST_DIR_PARTS = {"test", "tests", "spec", "specs", "__tests__"}
_ENTRYPOINT_STEMS = {
    "main", "app", "application", "index", "cli", "manage", "setup",
    "conftest", "settings", "config", "wsgi", "asgi", "server", "run",
    "__init__", "__main__",
}

TODO_THRESHOLD = 10
LARGE_FILE_LINES = 800
LOW_TEST_RATIO = 0.05
MODERATE_TEST_RATIO = 0.15
MIN_CODE_FILES_FOR_TEST_RULE = 10
MAX_EVIDENCE = 20


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    if any(part.lower() in _TEST_DIR_PARTS for part in p.parts[:-1]):
        return True
    name = p.name.lower()
    stem = name.rsplit(".", 1)[0]
    return (stem.startswith("test_") or stem.endswith("_test")
            or ".spec." in name or ".test." in name
            or stem.endswith("test") and stem != "test" and name.endswith(".java"))


@registry.register
class TechDebtAnalyzer(Analyzer):
    id = "tech_debt"
    name = "Technical debt analyzer"
    depends_on = ("module_graph",)

    def analyze(self, ctx: ProjectContext) -> AnalyzerResult:
        marker_counts: Counter[str] = Counter()
        marker_samples: list[Evidence] = []
        line_counts: dict[str, int] = {}
        code_files: list[str] = []
        test_files: list[str] = []

        for record in ctx.files:
            if record.language not in _SOURCE_LANGUAGES:
                continue
            (test_files if is_test_file(record.path)
             else code_files).append(record.path)
            text = read_text(ctx, record)
            if text is None:
                continue
            lines = text.splitlines()
            line_counts[record.path] = len(lines)
            for lineno, line in enumerate(lines, start=1):
                if _MARKER.search(line):
                    marker_counts[record.path] += 1
                    if len(marker_samples) < MAX_EVIDENCE:
                        marker_samples.append(Evidence(
                            file_path=record.path,
                            line_start=lineno,
                            snippet=line.strip()[:200],
                        ))

        findings: list[Finding] = []
        findings += self._test_ratio(code_files, test_files)
        findings += self._todo_density(marker_counts, marker_samples)
        findings += self._dead_files(ctx, code_files)
        findings += self._god_files(line_counts)
        return AnalyzerResult(findings=findings)

    def _test_ratio(self, code_files, test_files) -> list[Finding]:
        if len(code_files) < MIN_CODE_FILES_FOR_TEST_RULE:
            return []
        ratio = len(test_files) / len(code_files)
        if ratio >= MODERATE_TEST_RATIO:
            return []
        severity = (Severity.HIGH if ratio < LOW_TEST_RATIO
                    else Severity.MEDIUM)
        return [Finding(
            analyzer_id=self.id,
            rule_id="DEBT-TESTS-001",
            category=FindingCategory.TESTING,
            severity=severity,
            title=(
                f"Test coverage signal is "
                f"{'near zero' if severity == Severity.HIGH else 'low'}: "
                f"{len(test_files)} test files for {len(code_files)} "
                "source files"
            ),
            description=(
                f"Test-to-source file ratio is {ratio:.2f}. Migration "
                "without a test safety net means behavior cannot be "
                "verified before and after each phase — every regression "
                "is discovered in production. Building characterization "
                "tests around the modules in the earliest migration waves "
                "should be phase zero of the roadmap."
            ),
            evidence=[Evidence(
                source=EvidenceSource.HEURISTIC,
                detail=(f"test_files={len(test_files)}, "
                        f"code_files={len(code_files)}, ratio={ratio:.2f}"),
            )],
            metadata={"ratio": round(ratio, 3),
                      "test_files": len(test_files),
                      "code_files": len(code_files)},
        )]

    def _todo_density(self, counts, samples) -> list[Finding]:
        total = sum(counts.values())
        if total < TODO_THRESHOLD:
            return []
        return [Finding(
            analyzer_id=self.id,
            rule_id="DEBT-TODO-001",
            category=FindingCategory.TECHNICAL_DEBT,
            severity=Severity.LOW,
            title=f"{total} TODO/FIXME/HACK markers across "
                  f"{len(counts)} files",
            description=(
                "Self-declared debt markers left by the original authors. "
                "Individually minor; collectively a map of the places the "
                "team already knew were fragile — worth cross-referencing "
                "against the migration waves."
            ),
            evidence=samples,
            metadata={"total": total,
                      "by_file": dict(counts.most_common(50))},
        )]

    def _dead_files(self, ctx: ProjectContext, code_files) -> list[Finding]:
        graph: ModuleGraph | None = ctx.get_artifact("module_graph")
        if graph is None:
            return []
        imported = {e.target for e in graph.internal_edges}
        dead = []
        for path in code_files:
            if path not in graph.files or path in imported:
                continue
            stem = PurePosixPath(path).name.rsplit(".", 1)[0].lower()
            if stem in _ENTRYPOINT_STEMS:
                continue
            dead.append(path)
        if not dead:
            return []
        return [Finding(
            analyzer_id=self.id,
            rule_id="DEBT-DEAD-001",
            category=FindingCategory.TECHNICAL_DEBT,
            severity=Severity.MEDIUM,
            title=f"{len(dead)} source files are imported by nothing",
            description=(
                "No other module imports these files, and they do not "
                "look like entry points. Candidates for deletion before "
                "migration begins — migrating dead code is pure waste, "
                "and confirming each file's status shrinks the true scope "
                "of the program. Verify against dynamic loading and "
                "reflection before deleting."
            ),
            evidence=[Evidence(file_path=p) for p in dead[:MAX_EVIDENCE]],
            metadata={"count": len(dead), "files": dead},
        )]

    def _god_files(self, line_counts) -> list[Finding]:
        large = sorted(
            ((p, n) for p, n in line_counts.items()
             if n > LARGE_FILE_LINES and not is_test_file(p)),
            key=lambda x: -x[1],
        )
        if not large:
            return []
        return [Finding(
            analyzer_id=self.id,
            rule_id="DEBT-LARGE-001",
            category=FindingCategory.TECHNICAL_DEBT,
            severity=Severity.MEDIUM,
            title=f"{len(large)} files exceed {LARGE_FILE_LINES} lines",
            description=(
                "Files this large concentrate many responsibilities and "
                "cannot be migrated incrementally — they must be split or "
                "moved whole, and moving them whole drags their entire "
                "dependency surface into one phase. Splitting the largest "
                "before migration reduces phase risk."
            ),
            evidence=[
                Evidence(file_path=p, detail=f"{n} lines")
                for p, n in large[:MAX_EVIDENCE]
            ],
            metadata={"files": [
                {"path": p, "lines": n} for p, n in large
            ]},
        )]
