"""Module graph tests: extractors on real grammar output, resolvers on
tricky layouts, then the analyzer end-to-end on a polyglot project."""

from pathlib import Path

import pytest

from legacylens.analyzers.module_graph import ModuleGraphAnalyzer
from legacylens.domain.models import FileRecord, ProjectContext
from legacylens.parsing.ast.extract import (
    RawImport,
    extract_java,
    extract_javascript,
    extract_python,
)
from legacylens.parsing.ast.factory import parser_for
from legacylens.parsing.ast.resolvers import (
    JavaImportResolver,
    JsImportResolver,
    PythonImportResolver,
)


def parse(language: str, source: str, path: str = "x"):
    return parser_for(language, path).parse(source.encode()).root_node


class TestPythonExtraction:
    def test_import_forms(self):
        root = parse("python", (
            "import os\n"
            "import os.path as osp\n"
            "from corp.billing import invoice, ledger\n"
            "from .. import util\n"
            "from .helpers import fmt\n"
        ))
        imports = extract_python(root)
        specs = [(i.spec, i.relative_level, i.from_names) for i in imports]
        assert ("os", 0, ()) in specs
        assert ("os.path", 0, ()) in specs
        assert ("corp.billing", 0, ("invoice", "ledger")) in specs
        assert ("", 2, ("util",)) in specs
        assert ("helpers", 1, ("fmt",)) in specs
        assert imports[0].line == 1


class TestJavaExtraction:
    def test_import_forms(self):
        root = parse("java", (
            "import com.corp.billing.Invoice;\n"
            "import com.corp.util.*;\n"
            "import static org.junit.Assert.assertTrue;\n"
        ))
        imports = extract_java(root)
        assert (imports[0].spec, imports[0].wildcard) == (
            "com.corp.billing.Invoice", False)
        assert (imports[1].spec, imports[1].wildcard) == (
            "com.corp.util", True)
        assert imports[2].spec == "org.junit.Assert.assertTrue"


class TestJsExtraction:
    def test_import_forms(self):
        root = parse("javascript", (
            "import React from 'react';\n"
            "const utils = require('./utils');\n"
            "export { x } from '../lib/x';\n"
            "const dyn = import('./dyn');\n"
        ))
        specs = [i.spec for i in extract_javascript(root)]
        assert specs == ["react", "./utils", "../lib/x", "./dyn"]

    def test_tsx_parses_with_tsx_grammar(self):
        root = parse(
            "typescript",
            "import { App } from './App';\nconst x = <App prop={1} />;\n",
            path="ui/Main.tsx",
        )
        assert not root.has_error
        assert extract_javascript(root)[0].spec == "./App"


class TestPythonResolver:
    FILES = [
        "src/corp/__init__.py",
        "src/corp/api.py",
        "src/corp/billing/__init__.py",
        "src/corp/billing/invoice.py",
        "src/corp/util.py",
    ]

    def test_dotted_suffix_ignores_src_root(self):
        r = PythonImportResolver(self.FILES)
        res = r.resolve("src/corp/api.py",
                        RawImport("corp.billing.invoice", 1))
        assert res.internal and res.target == "src/corp/billing/invoice.py"

    def test_from_import_resolves_named_module(self):
        r = PythonImportResolver(self.FILES)
        res = r.resolve("src/corp/api.py",
                        RawImport("corp.billing", 1,
                                  from_names=("invoice",)))
        assert res.target == "src/corp/billing/invoice.py"

    def test_relative_up_level(self):
        r = PythonImportResolver(self.FILES)
        res = r.resolve("src/corp/billing/invoice.py",
                        RawImport("", 1, relative_level=2,
                                  from_names=("util",)))
        assert res.internal and res.target == "src/corp/util.py"

    def test_broken_relative(self):
        r = PythonImportResolver(self.FILES)
        res = r.resolve("src/corp/api.py",
                        RawImport("ghost", 1, relative_level=1))
        assert res.broken

    def test_external(self):
        r = PythonImportResolver(self.FILES)
        assert not r.resolve("src/corp/api.py", RawImport("flask", 1)).internal


class TestJavaResolver:
    FILES = [
        "backend/src/main/java/com/corp/billing/Invoice.java",
        "backend/src/main/java/com/corp/util/Strings.java",
    ]

    def test_classpath_suffix(self):
        r = JavaImportResolver(self.FILES)
        res = r.resolve("x", RawImport("com.corp.billing.Invoice", 1))
        assert res.internal and res.target.endswith("Invoice.java")

    def test_static_import_falls_back_to_class(self):
        r = JavaImportResolver(self.FILES)
        res = r.resolve("x", RawImport("com.corp.util.Strings.trim", 1))
        assert res.internal and res.target.endswith("Strings.java")

    def test_wildcard_resolves_to_package_dir(self):
        r = JavaImportResolver(self.FILES)
        res = r.resolve("x", RawImport("com.corp.billing", 1, wildcard=True))
        assert res.internal
        assert res.target.endswith("com/corp/billing")

    def test_external(self):
        r = JavaImportResolver(self.FILES)
        assert not r.resolve(
            "x", RawImport("org.springframework.core.Ordered", 1)).internal


class TestJsResolver:
    FILES = ["web/src/app.js", "web/src/utils.ts", "web/src/lib/index.js"]

    def test_relative_with_extension_probe(self):
        r = JsImportResolver(self.FILES)
        res = r.resolve("web/src/app.js", RawImport("./utils", 1))
        assert res.internal and res.target == "web/src/utils.ts"

    def test_directory_index_probe(self):
        r = JsImportResolver(self.FILES)
        res = r.resolve("web/src/app.js", RawImport("./lib", 1))
        assert res.target == "web/src/lib/index.js"

    def test_scoped_package_normalized(self):
        r = JsImportResolver(self.FILES)
        res = r.resolve("web/src/app.js",
                        RawImport("@corp/ui/Button", 1))
        assert not res.internal and res.target == "@corp/ui"

    def test_broken_relative(self):
        r = JsImportResolver(self.FILES)
        assert r.resolve("web/src/app.js", RawImport("./ghost", 1)).broken


class TestModuleGraphAnalyzer:
    def make_project(self, root: Path) -> ProjectContext:
        layout = {
            "src/corp/__init__.py": "",
            "src/corp/api.py": (
                "import flask\n"
                "from corp.billing import invoice\n"
                "from .ghost import x\n"
            ),
            "src/corp/billing/__init__.py": "",
            "src/corp/billing/invoice.py": "from .. import util\n",
            "src/corp/util.py": "",
            "src/corp/broken.py": "def f(:\n",
            "web/app.js": "import './style'\nimport react from 'react'\n",
            "web/style.js": "",
            "jsvc/src/com/corp/A.java":
                "import com.corp.B;\nclass A {}\n",
            "jsvc/src/com/corp/B.java": "class B {}\n",
        }
        files = []
        for path, content in layout.items():
            full = root / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
            language = {"py": "python", "js": "javascript",
                        "java": "java"}[path.rsplit(".", 1)[1]]
            files.append(FileRecord(
                path=path, size_bytes=len(content), language=language))
        return ProjectContext(project_id="p1", root=root, files=files)

    def test_end_to_end(self, tmp_path):
        ctx = self.make_project(tmp_path)
        result = ModuleGraphAnalyzer().analyze(ctx)
        graph = result.artifact

        assert len(graph.files) == 10
        internal = {(e.source, e.target) for e in graph.internal_edges}
        assert ("src/corp/api.py", "src/corp/billing/invoice.py") in internal
        assert ("src/corp/billing/invoice.py", "src/corp/util.py") in internal
        assert ("web/app.js", "web/style.js") in internal
        assert ("jsvc/src/com/corp/A.java",
                "jsvc/src/com/corp/B.java") in internal

        externals = graph.external_usage()
        assert externals["flask"] == 1 and externals["react"] == 1

        rules = {f.rule_id for f in result.findings}
        assert rules == {"AST-SYNTAX-001", "AST-IMPORT-001"}

        syntax = next(f for f in result.findings
                      if f.rule_id == "AST-SYNTAX-001")
        assert syntax.metadata["files"] == ["src/corp/broken.py"]

        broken = next(f for f in result.findings
                      if f.rule_id == "AST-IMPORT-001")
        assert broken.evidence[0].file_path == "src/corp/api.py"
        assert broken.evidence[0].line_start == 3

    def test_reverse_queries(self, tmp_path):
        ctx = self.make_project(tmp_path)
        graph = ModuleGraphAnalyzer().analyze(ctx).artifact
        assert "src/corp/api.py" in graph.importers_of(
            "src/corp/billing/invoice.py")
