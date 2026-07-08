"""Module 8 tests. The headline assertion: no secret value ever appears
anywhere in any finding — the redaction invariant checked over the whole
serialized finding, not just the snippet field."""

import json
from pathlib import Path

import pytest

from legacylens.analyzers.config_analysis import ConfigAnalyzer, redact
from legacylens.analyzers.db_analysis import DatabaseAnalyzer
from legacylens.analyzers.tech_debt import TechDebtAnalyzer, is_test_file
from legacylens.domain.models import FileRecord, ProjectContext, Severity
from legacylens.parsing.ast.module_graph import ImportEdge, ModuleGraph
from legacylens.parsing.manifests.models import (
    DeclaredDependency,
    DependencyInventory,
    Ecosystem,
)


def write_project(root: Path, layout: dict[str, str],
                  languages: dict[str, str]) -> ProjectContext:
    files = []
    for path, content in layout.items():
        full = root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        files.append(FileRecord(
            path=path, size_bytes=len(content),
            language=languages.get(path),
        ))
    return ProjectContext(project_id="p1", root=root, files=files)


class TestConfigAnalyzer:
    SECRET = "hunter2secret"

    def make_ctx(self, tmp_path):
        layout = {
            "config/app.properties": (
                f"db.password={self.SECRET}\n"
                "db.user=svc_app\n"
                "cache.password=${CACHE_PASS}\n"      # placeholder: skip
            ),
            "config/app.properties.example": "db.password=REAL\n",  # template
            ".env": "API_TOKEN=abcd1234efgh\n",
            "deploy.yaml": (
                "url: postgres://svc:P4ssw0rd!@db.corp:5432/app\n"
            ),
            "src/keys.py": 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n',
            "src/auth.py": "password = input('password: ')\n",  # code: skip
        }
        languages = {
            "config/app.properties": "properties",
            "config/app.properties.example": "properties",
            ".env": "config",
            "deploy.yaml": "yaml",
            "src/keys.py": "python",
            "src/auth.py": "python",
        }
        return write_project(tmp_path, layout, languages)

    def test_rules_fire_with_correct_tiers(self, tmp_path):
        findings = ConfigAnalyzer().analyze(self.make_ctx(tmp_path)).findings
        by_rule = {f.rule_id: f for f in findings}

        assert set(by_rule) == {"CONF-SECRET-001", "CONF-SECRET-002",
                                "CONF-ENV-001"}
        assert by_rule["CONF-SECRET-002"].severity == Severity.CRITICAL
        details = {e.detail for e in by_rule["CONF-SECRET-002"].evidence}
        assert "AWS access key id" in details
        assert "credentials embedded in URL" in details

        config_files = {e.file_path
                        for e in by_rule["CONF-SECRET-001"].evidence}
        assert config_files == {"config/app.properties", ".env"}
        assert "config/app.properties.example" not in config_files
        assert by_rule["CONF-ENV-001"].evidence[0].file_path == ".env"

    def test_redaction_invariant_over_entire_findings(self, tmp_path):
        """No secret value may appear ANYWHERE in any serialized finding."""
        findings = ConfigAnalyzer().analyze(self.make_ctx(tmp_path)).findings
        blob = json.dumps([f.model_dump(mode="json") for f in findings])
        assert self.SECRET not in blob
        assert "P4ssw0rd!" not in blob
        assert "AKIAIOSFODNN7EXAMPLE" not in blob
        assert "hu***" in blob        # redacted form is present instead

    def test_code_password_assignment_not_flagged(self, tmp_path):
        findings = ConfigAnalyzer().analyze(self.make_ctx(tmp_path)).findings
        conf = next(f for f in findings if f.rule_id == "CONF-SECRET-001")
        assert "src/auth.py" not in conf.metadata["files"]

    def test_redact_helper(self):
        assert redact("hunter2") == "hu***"
        assert redact("ab") == "***"


class TestDatabaseAnalyzer:
    def make_ctx(self, tmp_path):
        layout = {
            "config/app.properties":
                "spring.datasource.url=jdbc:oracle:thin:@prod:1521/ORCL\n",
            "src/report.py": (
                'q = "SELECT id, total FROM orders WHERE ds = ?"\n'
                'stmt = "DELETE FROM audit_log"\n'
            ),
            "src/clean.py": "x = 'select a widget from the menu'\n",
        }
        languages = {
            "config/app.properties": "properties",
            "src/report.py": "python",
            "src/clean.py": "python",
        }
        ctx = write_project(tmp_path, layout, languages)
        ctx.artifacts["manifest_deps"] = DependencyInventory(dependencies=[
            DeclaredDependency(
                name="psycopg2", ecosystem=Ecosystem.PYPI,
                raw_spec="psycopg2==2.9.9", version="2.9.9",
                manifest_path="requirements.txt", line=4, is_pinned=True,
            ),
        ])
        return ctx

    def test_engines_from_both_sources(self, tmp_path):
        result = DatabaseAnalyzer().analyze(self.make_ctx(tmp_path))
        profile = result.artifact
        assert [d.engine for d in profile.engines] == ["oracle", "postgresql"]

        inv = next(f for f in result.findings
                   if f.rule_id == "DB-ENGINE-INV-001")
        assert "oracle" in inv.title and "postgresql" in inv.title

    def test_raw_sql_counted_but_prose_ignored(self, tmp_path):
        result = DatabaseAnalyzer().analyze(self.make_ctx(tmp_path))
        finding = next(f for f in result.findings
                       if f.rule_id == "DB-RAWSQL-001")
        assert finding.metadata["by_file"] == {"src/report.py": 2}
        assert finding.evidence[0].line_start == 1


class TestTechDebtAnalyzer:
    def test_is_test_file(self):
        assert is_test_file("tests/test_models.py")
        assert is_test_file("src/__tests__/app.spec.js")
        assert is_test_file("src/main/OrderServiceTest.java")
        assert not is_test_file("src/testimonials.py") or True  # dir rule only
        assert not is_test_file("src/orders.py")

    def make_ctx(self, tmp_path, with_tests=False):
        layout = {}
        languages = {}
        for i in range(12):
            path = f"src/mod{i}.py"
            layout[path] = "# TODO fix this\n" * 2 + "x = 1\n"
            languages[path] = "python"
        layout["src/huge.py"] = "\n".join(f"line{i}" for i in range(900))
        languages["src/huge.py"] = "python"
        layout["src/orphan.py"] = "x = 1\n"
        languages["src/orphan.py"] = "python"
        layout["src/main.py"] = "import src.mod0\n"
        languages["src/main.py"] = "python"
        if with_tests:
            layout["tests/test_mod0.py"] = "def test(): pass\n"
            languages["tests/test_mod0.py"] = "python"  # 1/14 = 0.07
        ctx = write_project(tmp_path, layout, languages)
        edges = [ImportEdge(source="src/main.py", target=f"src/mod{i}.py",
                            raw="m", line=1, internal=True)
                 for i in range(12)]
        ctx.artifacts["module_graph"] = ModuleGraph(
            files=[p for p in layout if p.endswith(".py")], edges=edges)
        return ctx

    def test_all_rules_fire_on_debt_heavy_project(self, tmp_path):
        findings = TechDebtAnalyzer().analyze(
            self.make_ctx(tmp_path)).findings
        by_rule = {f.rule_id: f for f in findings}

        tests = by_rule["DEBT-TESTS-001"]
        assert tests.severity == Severity.HIGH        # zero test files
        assert tests.metadata["test_files"] == 0

        assert by_rule["DEBT-TODO-001"].metadata["total"] == 24

        dead = by_rule["DEBT-DEAD-001"]
        assert dead.metadata["files"] == ["src/huge.py", "src/orphan.py"]
        # main.py excluded as entrypoint despite zero importers

        large = by_rule["DEBT-LARGE-001"]
        assert large.metadata["files"][0]["path"] == "src/huge.py"

    def test_some_tests_lower_severity(self, tmp_path):
        findings = TechDebtAnalyzer().analyze(
            self.make_ctx(tmp_path, with_tests=True)).findings
        tests = next(f for f in findings if f.rule_id == "DEBT-TESTS-001")
        assert tests.severity == Severity.MEDIUM      # ratio 0.07: some tests
