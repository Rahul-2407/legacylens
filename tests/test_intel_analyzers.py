"""Module 6 tests: detection sources, EOL judgment with a frozen clock,
vulnerability severity mapping, and every degradation path."""

from datetime import date
from pathlib import Path

import pytest

from legacylens.analyzers.dep_vulns import VulnerabilityAnalyzer
from legacylens.analyzers.tech_detection import (
    TechDetectionAnalyzer,
    TechKind,
)
from legacylens.analyzers.tech_eol import EolAnalyzer, match_cycle
from legacylens.core.exceptions import ExternalEvidenceError
from legacylens.domain.models import FileRecord, ProjectContext, Severity
from legacylens.evidence.eol import ProductCycle
from legacylens.evidence.osv import Vulnerability
from legacylens.parsing.manifests.models import (
    DeclaredDependency,
    DependencyInventory,
    Ecosystem,
)

TODAY = date(2026, 7, 2)


def dep(name, version=None, pinned=True, eco=Ecosystem.PYPI,
        manifest="requirements.txt", line=1):
    return DeclaredDependency(
        name=name, ecosystem=eco, raw_spec=f"{name}=={version or '?'}",
        version=version, manifest_path=manifest, line=line, is_pinned=pinned,
    )


def make_ctx(tmp_path: Path, files=(), inventory=None,
             profile=None) -> ProjectContext:
    ctx = ProjectContext(project_id="p1", root=tmp_path, files=list(files))
    if inventory is not None:
        ctx.artifacts["manifest_deps"] = inventory
    if profile is not None:
        ctx.artifacts["tech_detection"] = profile
    return ctx


class TestTechDetection:
    def test_frameworks_from_dependencies(self, tmp_path):
        inventory = DependencyInventory(dependencies=[
            dep("django", "1.11.29"),
            dep("org.springframework:spring-core", "4.3.9.RELEASE",
                eco=Ecosystem.MAVEN, manifest="pom.xml"),
            dep("react", "16.8.0", eco=Ecosystem.NPM,
                manifest="package.json"),
            dep("unknown-lib", "1.0"),
        ])
        ctx = make_ctx(tmp_path, inventory=inventory)
        profile = TechDetectionAnalyzer().analyze(ctx).artifact

        by_name = {t.name: t for t in profile.technologies}
        assert by_name["Django"].eol_product == "django"
        assert by_name["Django"].version == "1.11.29"
        assert by_name["Spring Framework"].eol_product == "spring-framework"
        assert by_name["React"].kind == TechKind.FRAMEWORK
        assert "unknown-lib" not in by_name

        # evidence chain back to the manifest line
        assert by_name["Django"].evidence[0].file_path == "requirements.txt"

    def test_runtimes_from_dockerfile(self, tmp_path):
        (tmp_path / "Dockerfile").write_text(
            "FROM python:2.7-alpine AS build\nRUN pip install .\n"
            "FROM nginx:1.14\n"
        )
        ctx = make_ctx(
            tmp_path,
            files=[FileRecord(path="Dockerfile", size_bytes=1,
                              language="dockerfile")],
            inventory=DependencyInventory(),
        )
        profile = TechDetectionAnalyzer().analyze(ctx).artifact
        by_name = {t.name: t for t in profile.technologies}

        python = by_name["Python runtime"]
        assert python.version == "2.7"          # tag suffix stripped
        assert python.eol_product == "python"
        assert python.evidence[0].line_start == 1
        assert by_name["nginx"].version == "1.14"
        assert by_name["Docker"].kind == TechKind.BUILD_TOOL

    def test_languages_need_critical_mass(self, tmp_path):
        files = [FileRecord(path=f"src/f{i}.py", size_bytes=1,
                            language="python") for i in range(3)]
        files.append(FileRecord(path="one.go", size_bytes=1, language="go"))
        ctx = make_ctx(tmp_path, files=files,
                       inventory=DependencyInventory())
        profile = TechDetectionAnalyzer().analyze(ctx).artifact
        names = {t.name for t in profile.technologies}
        assert "python" in names and "go" not in names


CYCLES = [
    ProductCycle(cycle="1.11", eol_date=date(2020, 4, 1), latest="1.11.29"),
    ProductCycle(cycle="4.2", eol_date=date(2026, 12, 1), latest="4.2.17"),
    ProductCycle(cycle="5.2", eol_date=date(2029, 4, 1), latest="5.2.1"),
    ProductCycle(cycle="0.9", eol_flag=True),
]


class FakeEolClient:
    def __init__(self, cycles=CYCLES, error=False):
        self._cycles, self._error = cycles, error
        self.calls = []

    def get_cycles(self, product):
        self.calls.append(product)
        if self._error:
            raise ExternalEvidenceError("network down")
        return self._cycles if product != "unknown-product" else None

    @staticmethod
    def product_url(product):
        return f"https://endoflife.date/{product}"


def profile_with(name, version, product="django"):
    from legacylens.analyzers.tech_detection import (
        DetectedTechnology, TechnologyProfile,
    )
    from legacylens.domain.models import Evidence
    return TechnologyProfile(technologies=[DetectedTechnology(
        name=name, kind=TechKind.FRAMEWORK, version=version,
        eol_product=product,
        evidence=(Evidence(file_path="requirements.txt", line_start=2,
                           snippet=f"{name}=={version}"),),
    )])


class TestMatchCycle:
    def test_longest_prefix_on_dot_boundary(self):
        assert match_cycle("1.11.29", CYCLES).cycle == "1.11"
        assert match_cycle("4.2", CYCLES).cycle == "4.2"
        assert match_cycle("4.20.1", CYCLES) is None   # not '4.2' + '.'
        assert match_cycle("9.9.9", CYCLES) is None


class TestEolAnalyzer:
    def test_ancient_eol_is_critical_with_dual_evidence(self, tmp_path):
        ctx = make_ctx(tmp_path, profile=profile_with("Django", "1.11.29"))
        analyzer = EolAnalyzer(client=FakeEolClient(), today=TODAY)
        findings = analyzer.analyze(ctx).findings

        finding = next(f for f in findings if f.rule_id == "TECH-EOL-001")
        assert finding.severity == Severity.CRITICAL   # 6+ years past EOL
        assert finding.metadata["years_past_eol"] > 6
        sources = {e.source for e in finding.evidence}
        assert {"static_analysis", "external"} <= {str(s) for s in sources}
        assert any("endoflife.date/django" in (e.reference_url or "")
                   for e in finding.evidence)

    def test_approaching_eol_is_medium(self, tmp_path):
        ctx = make_ctx(tmp_path, profile=profile_with("Django", "4.2.17"))
        findings = EolAnalyzer(
            client=FakeEolClient(), today=TODAY).analyze(ctx).findings
        finding = findings[0]
        assert finding.rule_id == "TECH-EOL-002"
        assert finding.severity == Severity.MEDIUM

    def test_supported_cycle_yields_nothing(self, tmp_path):
        ctx = make_ctx(tmp_path, profile=profile_with("Django", "5.2.1"))
        assert EolAnalyzer(
            client=FakeEolClient(), today=TODAY).analyze(ctx).findings == []

    def test_flag_only_eol_is_high_not_critical(self, tmp_path):
        ctx = make_ctx(tmp_path, profile=profile_with("Flask", "0.9",
                                                      product="flask"))
        finding = EolAnalyzer(
            client=FakeEolClient(), today=TODAY).analyze(ctx).findings[0]
        assert finding.severity == Severity.HIGH      # no date => no years
        assert finding.metadata["years_past_eol"] is None

    def test_unknown_product_skipped_silently(self, tmp_path):
        ctx = make_ctx(tmp_path, profile=profile_with(
            "Corp Framework", "1.0", product="unknown-product"))
        assert EolAnalyzer(
            client=FakeEolClient(), today=TODAY).analyze(ctx).findings == []

    def test_network_failure_degrades_honestly(self, tmp_path):
        ctx = make_ctx(tmp_path, profile=profile_with("Django", "1.11.29"))
        findings = EolAnalyzer(
            client=FakeEolClient(error=True), today=TODAY
        ).analyze(ctx).findings
        assert [f.rule_id for f in findings] == ["EVID-UNAVAILABLE-001"]
        assert "django" in findings[0].metadata["products"]


class FakeOsvClient:
    def __init__(self, hits=None, error=False):
        self._hits = hits or {}
        self._error = error

    def query_batch(self, packages):
        if self._error:
            raise ExternalEvidenceError("network down")
        return [[v.id for v in self._hits.get(name, [])]
                for name, _, _ in packages]

    def query(self, name, ecosystem, version):
        return self._hits.get(name, [])


VULNS = [
    Vulnerability(id="GHSA-1", summary="RCE in templates",
                  aliases=("CVE-2019-0001",), severity_label="CRITICAL"),
    Vulnerability(id="GHSA-2", summary="Open redirect",
                  severity_label="MODERATE"),
]


class TestVulnerabilityAnalyzer:
    def test_finding_takes_worst_severity_and_cites_advisories(
            self, tmp_path):
        inventory = DependencyInventory(dependencies=[
            dep("flask", "0.12.4", line=3),
            dep("requests", "2.32.0"),
            dep("floaty", None, pinned=False),
        ])
        ctx = make_ctx(tmp_path, inventory=inventory)
        analyzer = VulnerabilityAnalyzer(
            client=FakeOsvClient(hits={"flask": VULNS}))
        findings = analyzer.analyze(ctx).findings

        assert len(findings) == 1
        finding = findings[0]
        assert finding.rule_id == "DEP-VULN-001"
        assert finding.severity == Severity.CRITICAL
        assert finding.metadata["vulnerability_ids"] == ["GHSA-1", "GHSA-2"]
        assert finding.evidence[0].file_path == "requirements.txt"
        assert finding.evidence[0].line_start == 3
        assert "osv.dev/vulnerability/GHSA-1" in finding.evidence[1].reference_url

    def test_unlabeled_vulns_default_to_medium(self, tmp_path):
        naked = [Vulnerability(id="OSV-9")]
        inventory = DependencyInventory(dependencies=[dep("x", "1.0")])
        ctx = make_ctx(tmp_path, inventory=inventory)
        finding = VulnerabilityAnalyzer(
            client=FakeOsvClient(hits={"x": naked})).analyze(ctx).findings[0]
        assert finding.severity == Severity.MEDIUM

    def test_no_pinned_deps_no_queries(self, tmp_path):
        inventory = DependencyInventory(
            dependencies=[dep("floaty", None, pinned=False)])
        ctx = make_ctx(tmp_path, inventory=inventory)
        assert VulnerabilityAnalyzer(
            client=FakeOsvClient(error=True)).analyze(ctx).findings == []

    def test_network_failure_degrades_honestly(self, tmp_path):
        inventory = DependencyInventory(dependencies=[dep("flask", "0.12.4")])
        ctx = make_ctx(tmp_path, inventory=inventory)
        findings = VulnerabilityAnalyzer(
            client=FakeOsvClient(error=True)).analyze(ctx).findings
        assert [f.rule_id for f in findings] == ["EVID-UNAVAILABLE-001"]
