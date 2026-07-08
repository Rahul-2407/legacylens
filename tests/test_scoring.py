"""Scoring engine tests. Headline invariants: the readiness breakdown
sums exactly to the score (explainability enforced), caps hold under
finding floods, and identical inputs always produce identical output."""

from pathlib import Path

import pytest

from legacylens.domain.models import (
    Evidence,
    Finding,
    FindingCategory,
    FileRecord,
    ProjectContext,
    Severity,
)
from legacylens.graph.algorithms import ArchitectureReport, ModuleMetrics
from legacylens.scoring.engine import compute_scorecard

def finding(severity, rule="R-1", path="src/a.py",
            category=FindingCategory.DEPENDENCY) -> Finding:
    return Finding(
        analyzer_id="x", rule_id=rule, category=category,
        severity=severity, title=f"{rule} {severity}", description="d",
        evidence=[Evidence(file_path=path, line_start=1)],
    )


def make_ctx(tmp_path: Path, metrics: dict[str, ModuleMetrics] | None = None,
             sizes: dict[str, int] | None = None) -> ProjectContext:
    sizes = sizes or {}
    ctx = ProjectContext(
        project_id="p1", root=tmp_path,
        files=[FileRecord(path=p, size_bytes=s) for p, s in sizes.items()],
    )
    if metrics is not None:
        ctx.artifacts["architecture"] = ArchitectureReport(
            cycles=[], waves=[], metrics=metrics)
    return ctx


class TestReadiness:
    def test_breakdown_sums_exactly_to_score(self, tmp_path):
        findings = [
            finding(Severity.CRITICAL), finding(Severity.HIGH),
            finding(Severity.HIGH), finding(Severity.MEDIUM),
            finding(Severity.LOW), finding(Severity.INFO),
        ]
        card = compute_scorecard(findings, make_ctx(tmp_path))
        total_delta = sum(c.delta for c in card.readiness.components)
        assert card.readiness.value == 100 + total_delta
        assert card.readiness.value == 100 - 15 - 16 - 3 - 1  # = 65

    def test_caps_hold_under_finding_flood(self, tmp_path):
        findings = [finding(Severity.CRITICAL, rule=f"R-{i}")
                    for i in range(50)]
        card = compute_scorecard(findings, make_ctx(tmp_path))
        assert card.readiness.value == 100 - 45      # capped, not 0
        assert card.readiness.components[0].delta == -45

    def test_floor_is_zero(self, tmp_path):
        findings = (
            [finding(Severity.CRITICAL, rule=f"C{i}") for i in range(5)]
            + [finding(Severity.HIGH, rule=f"H{i}") for i in range(10)]
            + [finding(Severity.MEDIUM, rule=f"M{i}") for i in range(10)]
            + [finding(Severity.LOW, rule=f"L{i}") for i in range(10)]
        )
        card = compute_scorecard(findings, make_ctx(tmp_path))
        assert card.readiness.value == 0
        assert card.readiness.value >= 0

    def test_clean_project_scores_100(self, tmp_path):
        card = compute_scorecard([], make_ctx(tmp_path))
        assert card.readiness.value == 100
        assert card.readiness.components == []

    def test_determinism(self, tmp_path):
        findings = [finding(Severity.HIGH), finding(Severity.MEDIUM)]
        ctx = make_ctx(tmp_path)
        assert (compute_scorecard(findings, ctx).model_dump()
                == compute_scorecard(findings, ctx).model_dump())


class TestModuleScores:
    def test_components_and_bands(self, tmp_path):
        metrics = {
            "src/hub.py": ModuleMetrics(path="src/hub.py", fan_in=15,
                                        fan_out=5, wave=1, cycle_id=0),
            "src/leaf.py": ModuleMetrics(path="src/leaf.py", fan_in=0,
                                         fan_out=1, wave=0),
        }
        sizes = {"src/hub.py": 60 * 1024, "src/leaf.py": 1024}
        ctx = make_ctx(tmp_path, metrics, sizes)
        card = compute_scorecard(
            [finding(Severity.CRITICAL, path="src/hub.py")], ctx)

        hub = card.module_scores["src/hub.py"]
        assert hub.size_component == 1.0            # saturated at 50 KB
        assert hub.coupling_component == 1.0        # fan 20 saturates
        assert hub.in_cycle
        # 100*(0.35 + 0.35 + 0.2*0.5 + 0.1) = 90
        assert hub.complexity == 90 and hub.band == "high"

        leaf = card.module_scores["src/leaf.py"]
        assert leaf.band == "low" and not leaf.in_cycle


class TestRiskAndSelection:
    def test_matrix_and_top_risks_order(self, tmp_path):
        findings = [
            finding(Severity.HIGH, rule="H-1",
                    category=FindingCategory.SECURITY),
            finding(Severity.CRITICAL, rule="C-1",
                    category=FindingCategory.TECHNOLOGY),
            finding(Severity.MEDIUM, rule="M-1",
                    category=FindingCategory.SECURITY),
        ]
        card = compute_scorecard(findings, make_ctx(tmp_path))
        assert card.risk_matrix["high"]["security"] == 1
        assert card.risk_matrix["critical"]["technology"] == 1
        assert [r["rule_id"] for r in card.top_risks] == ["C-1", "H-1"]

    def test_quick_wins_exclude_hot_and_late_wave_files(self, tmp_path):
        metrics = {
            "src/easy.py": ModuleMetrics(path="src/easy.py", wave=0),
            "src/late.py": ModuleMetrics(path="src/late.py", wave=2),
            "src/burnt.py": ModuleMetrics(path="src/burnt.py", wave=0),
            "pkg/__init__.py": ModuleMetrics(path="pkg/__init__.py", wave=0),
        }
        sizes = {p: 500 for p in metrics}
        ctx = make_ctx(tmp_path, metrics, sizes)
        card = compute_scorecard(
            [finding(Severity.HIGH, path="src/burnt.py")], ctx)
        assert card.quick_wins == ["src/easy.py"]

    def test_high_risk_reasons(self, tmp_path):
        metrics = {"src/hub.py": ModuleMetrics(
            path="src/hub.py", fan_in=18, fan_out=6, wave=1, cycle_id=0)}
        ctx = make_ctx(tmp_path, metrics, {"src/hub.py": 60 * 1024})
        card = compute_scorecard(
            [finding(Severity.CRITICAL, path="src/hub.py"),
             finding(Severity.HIGH, rule="R-2", path="src/hub.py")], ctx)
        entry = card.high_risk_modules[0]
        assert entry["path"] == "src/hub.py"
        assert "member of a dependency cycle" in entry["reasons"]
        assert "concentrated severe findings" in entry["reasons"]


class TestEffort:
    def make_scored_ctx(self, tmp_path):
        metrics = {
            "a.py": ModuleMetrics(path="a.py", wave=0),                # low
            "b.py": ModuleMetrics(path="b.py", fan_in=8, fan_out=4,
                                  wave=1),                             # med
            "c.py": ModuleMetrics(path="c.py", fan_in=15, fan_out=8,
                                  wave=2, cycle_id=0),                 # high
        }
        sizes = {"a.py": 500, "b.py": 30 * 1024, "c.py": 60 * 1024}
        return make_ctx(tmp_path, metrics, sizes)

    def test_effort_arithmetic_and_assumptions(self, tmp_path):
        ctx = self.make_scored_ctx(tmp_path)
        card = compute_scorecard([], ctx)
        e = card.effort
        base = (e.band_counts.get("low", 0) * 0.5
                + e.band_counts.get("medium", 0) * 2.0
                + e.band_counts.get("high", 0) * 5.0)
        assert e.expected_days == round(base * 1.15, 1)
        assert e.optimistic_days < e.expected_days < e.pessimistic_days
        assert any("heuristic model" in a for a in e.assumptions)
        assert e.multipliers_applied == []

    def test_no_tests_multiplier_applies(self, tmp_path):
        ctx = self.make_scored_ctx(tmp_path)
        debt = Finding(
            analyzer_id="tech_debt", rule_id="DEBT-TESTS-001",
            category=FindingCategory.TESTING, severity=Severity.HIGH,
            title="no tests", description="d",
            evidence=[Evidence(detail="ratio 0")],
        )
        with_tests_gap = compute_scorecard([debt], ctx).effort
        without = compute_scorecard([], ctx).effort
        assert with_tests_gap.expected_days == round(
            without.expected_days * 1.5, 1)
        assert any("×1.5" in m for m in with_tests_gap.multipliers_applied)
