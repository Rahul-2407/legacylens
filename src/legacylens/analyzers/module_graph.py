"""Module graph analyzer.

Parses every supported source file, extracts imports, resolves them
against the project, and publishes the ModuleGraph artifact that the
architecture and Neo4j layers (Module 7) consume.

Deterministic rules emitted here:

  AST-SYNTAX-001  source files with syntax errors (grouped per language)
  AST-IMPORT-001  relative imports that resolve to nothing (broken)
"""

import logging
from collections import defaultdict

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
from legacylens.parsing.ast.extract import EXTRACTORS
from legacylens.parsing.ast.factory import parser_for
from legacylens.parsing.ast.module_graph import ImportEdge, ModuleGraph
from legacylens.parsing.ast.resolvers import (
    JavaImportResolver,
    JsImportResolver,
    PythonImportResolver,
)

logger = logging.getLogger(__name__)

MAX_SOURCE_BYTES = 1024 * 1024
MAX_EVIDENCE_PER_FINDING = 20
_JS_LANGS = ("javascript", "typescript")


@registry.register
class ModuleGraphAnalyzer(Analyzer):
    id = "module_graph"
    name = "Module graph analyzer"

    def analyze(self, ctx: ProjectContext) -> AnalyzerResult:
        resolvers = {
            "python": PythonImportResolver(
                [f.path for f in ctx.files_by_language("python")]),
            "java": JavaImportResolver(
                [f.path for f in ctx.files_by_language("java")]),
            "js": JsImportResolver([f.path for f in ctx.files]),
        }

        graph = ModuleGraph()
        syntax_errors: dict[str, list[str]] = defaultdict(list)

        for record in ctx.files:
            language = record.language
            if (
                language not in EXTRACTORS
                or record.is_binary
                or record.size_bytes > MAX_SOURCE_BYTES
            ):
                continue
            parser = parser_for(language, record.path)
            if parser is None:
                continue

            source = (ctx.root / record.path).read_bytes()
            tree = parser.parse(source)
            graph.files.append(record.path)
            if tree.root_node.has_error:
                syntax_errors[language].append(record.path)
                # continue anyway: tree-sitter recovers, partial imports
                # are better than none for a legacy codebase

            resolver = resolvers["js" if language in _JS_LANGS
                                 else language]
            for imp in EXTRACTORS[language](tree.root_node):
                res = resolver.resolve(record.path, imp)
                graph.edges.append(ImportEdge(
                    source=record.path,
                    target=res.target,
                    raw=imp.spec or "." * imp.relative_level,
                    line=imp.line,
                    internal=res.internal,
                    broken=res.broken,
                ))

        findings = self._syntax_findings(syntax_errors)
        findings += self._broken_import_findings(graph)

        log_with_fields(
            logger, logging.INFO, "module graph built",
            files=len(graph.files),
            internal_edges=len(graph.internal_edges),
            external_specs=len(graph.external_usage()),
            broken=len(graph.broken_edges),
        )
        return AnalyzerResult(findings=findings, artifact=graph)

    def _syntax_findings(
        self, syntax_errors: dict[str, list[str]]
    ) -> list[Finding]:
        findings = []
        for language, paths in sorted(syntax_errors.items()):
            findings.append(Finding(
                analyzer_id=self.id,
                rule_id="AST-SYNTAX-001",
                category=FindingCategory.TECHNICAL_DEBT,
                severity=Severity.MEDIUM,
                title=f"{len(paths)} {language} files with syntax errors",
                description=(
                    f"{len(paths)} {language} source files fail to parse. "
                    "These may be dead code, template files, or written for "
                    "an older language version — each is a migration "
                    "question mark that inflates risk until triaged. "
                    "Import analysis for these files is partial."
                ),
                evidence=[
                    Evidence(file_path=p)
                    for p in paths[:MAX_EVIDENCE_PER_FINDING]
                ],
                metadata={"count": len(paths), "language": language,
                          "files": paths},
            ))
        return findings

    def _broken_import_findings(self, graph: ModuleGraph) -> list[Finding]:
        broken = graph.broken_edges
        if not broken:
            return []
        return [Finding(
            analyzer_id=self.id,
            rule_id="AST-IMPORT-001",
            category=FindingCategory.TECHNICAL_DEBT,
            severity=Severity.HIGH,
            title=f"{len(broken)} imports reference missing project files",
            description=(
                "These relative imports do not resolve to any file in the "
                "project. Either the code path is dead (never executed), "
                "the files exist only in some deployment environment, or "
                "the build is currently broken. Every one must be "
                "explained before migration — dead code should be deleted "
                "rather than migrated."
            ),
            evidence=[
                Evidence(
                    file_path=e.source,
                    line_start=e.line,
                    snippet=e.raw,
                    detail=f"unresolved target: {e.target}",
                )
                for e in broken[:MAX_EVIDENCE_PER_FINDING]
            ],
            metadata={"count": len(broken)},
        )]
