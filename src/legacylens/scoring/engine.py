"""Scoring engine — deterministic formulas over the complete finding set.

NOT an analyzer: analyzers see the project, scoring must see every finding,
which only exists after the pipeline completes. This module is the first
synthesis-tier stage; the LLM agents (Module 10) are the second. LLMs will
EXPLAIN these numbers, never produce them — language models are unreliable
at consistent numeric scoring, and an unexplainable score is worthless.

Every output is explainable by construction:
* the readiness score ships its component breakdown (what deducted what)
* every module complexity score ships its component values
* the effort estimate ships its assumptions as part of the artifact

All weights live in the constants below — one visible policy table.
"""

from collections import Counter, defaultdict
from pathlib import PurePosixPath

from pydantic import BaseModel, Field

from legacylens.domain.models import Finding, ProjectContext, Severity
from legacylens.graph.algorithms import ArchitectureReport

# ---------------------------------------------------------------- policy

READINESS_DEDUCTION = {          # per finding, capped per severity
    Severity.CRITICAL: 15,
    Severity.HIGH: 8,
    Severity.MEDIUM: 3,
    Severity.LOW: 1,
    Severity.INFO: 0,
}
READINESS_CAP = {                # max total deduction per severity class
    Severity.CRITICAL: 45,
    Severity.HIGH: 32,
    Severity.MEDIUM: 15,
    Severity.LOW: 8,
    Severity.INFO: 0,
}

FINDING_WEIGHT = {               # for per-module complexity
    Severity.CRITICAL: 5.0,
    Severity.HIGH: 3.0,
    Severity.MEDIUM: 1.0,
    Severity.LOW: 0.5,
    Severity.INFO: 0.0,
}

# complexity = 100 * (w_size*size + w_coupling*coupling + w_findings*f + w_cycle*c)
W_SIZE, W_COUPLING, W_FINDINGS, W_CYCLE = 0.35, 0.35, 0.20, 0.10
SIZE_NORM_BYTES = 50 * 1024      # a 50 KB source file saturates size
COUPLING_NORM = 20               # fan_in + fan_out of 20 saturates coupling
FINDING_NORM = 10.0              # weighted finding load of 10 saturates

BAND_MEDIUM, BAND_HIGH = 30, 60
EFFORT_DAYS_PER_BAND = {"low": 0.5, "medium": 2.0, "high": 5.0}
NO_TESTS_MULTIPLIER = 1.5        # DEBT-TESTS-001 at HIGH
LOW_TESTS_MULTIPLIER = 1.25      # DEBT-TESTS-001 at MEDIUM
INTEGRATION_OVERHEAD = 0.15      # cross-module integration & verification
ESTIMATE_SPREAD = 0.30           # ±30% optimistic/pessimistic band

MAX_LISTED = 15

# ---------------------------------------------------------------- models


class ScoreComponent(BaseModel):
    name: str
    detail: str
    delta: int                   # signed contribution to the score


class ReadinessScore(BaseModel):
    value: int                   # 0..100, higher = more ready
    components: list[ScoreComponent] = Field(default_factory=list)


class ModuleScore(BaseModel):
    path: str
    complexity: int              # 0..100
    band: str                    # low | medium | high
    size_component: float
    coupling_component: float
    findings_component: float
    in_cycle: bool


class EffortEstimate(BaseModel):
    expected_days: float
    optimistic_days: float
    pessimistic_days: float
    band_counts: dict[str, int]
    multipliers_applied: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class ScoreCard(BaseModel):
    readiness: ReadinessScore
    module_scores: dict[str, ModuleScore] = Field(default_factory=dict)
    risk_matrix: dict[str, dict[str, int]] = Field(default_factory=dict)
    top_risks: list[dict] = Field(default_factory=list)
    quick_wins: list[str] = Field(default_factory=list)
    high_risk_modules: list[dict] = Field(default_factory=list)
    effort: EffortEstimate


# ---------------------------------------------------------------- engine


def _readiness(findings: list[Finding]) -> ReadinessScore:
    counts = Counter(f.severity for f in findings)
    components: list[ScoreComponent] = []
    score = 100
    for severity in (Severity.CRITICAL, Severity.HIGH,
                     Severity.MEDIUM, Severity.LOW):
        n = counts.get(severity, 0)
        if not n:
            continue
        deduction = min(n * READINESS_DEDUCTION[severity],
                        READINESS_CAP[severity])
        score -= deduction
        components.append(ScoreComponent(
            name=f"{severity}_findings",
            detail=(f"{n} {severity} finding(s) × "
                    f"{READINESS_DEDUCTION[severity]} "
                    f"(capped at {READINESS_CAP[severity]})"),
            delta=-deduction,
        ))
    return ReadinessScore(value=max(0, min(100, score)),
                          components=components)


def _findings_by_file(findings: list[Finding]) -> dict[str, float]:
    load: dict[str, float] = defaultdict(float)
    for finding in findings:
        weight = FINDING_WEIGHT[finding.severity]
        if not weight:
            continue
        for path in {e.file_path for e in finding.evidence if e.file_path}:
            load[path] += weight
    return load


def _module_scores(
    ctx: ProjectContext, findings: list[Finding]
) -> dict[str, ModuleScore]:
    arch: ArchitectureReport | None = ctx.get_artifact("architecture")
    metrics = arch.metrics if arch else {}
    load = _findings_by_file(findings)
    sizes = {f.path: f.size_bytes for f in ctx.files}

    scores: dict[str, ModuleScore] = {}
    for path in (metrics or sizes):
        m = metrics.get(path)
        size_c = min(1.0, sizes.get(path, 0) / SIZE_NORM_BYTES)
        coupling_c = (min(1.0, (m.fan_in + m.fan_out) / COUPLING_NORM)
                      if m else 0.0)
        findings_c = min(1.0, load.get(path, 0.0) / FINDING_NORM)
        in_cycle = bool(m and m.cycle_id is not None)
        complexity = round(100 * (
            W_SIZE * size_c + W_COUPLING * coupling_c
            + W_FINDINGS * findings_c + W_CYCLE * (1.0 if in_cycle else 0.0)
        ))
        band = ("high" if complexity >= BAND_HIGH
                else "medium" if complexity >= BAND_MEDIUM else "low")
        scores[path] = ModuleScore(
            path=path, complexity=complexity, band=band,
            size_component=round(size_c, 3),
            coupling_component=round(coupling_c, 3),
            findings_component=round(findings_c, 3),
            in_cycle=in_cycle,
        )
    return scores


def _risk_matrix(findings: list[Finding]):
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for finding in findings:
        matrix[str(finding.severity)][str(finding.category)] += 1
    top = sorted(
        (f for f in findings
         if f.severity in (Severity.CRITICAL, Severity.HIGH)),
        key=lambda f: (f.severity != Severity.CRITICAL, f.rule_id),
    )
    top_risks = [
        {"finding_id": f.finding_id, "rule_id": f.rule_id,
         "severity": str(f.severity), "title": f.title}
        for f in top[:MAX_LISTED]
    ]
    return {s: dict(c) for s, c in matrix.items()}, top_risks


def _effort(module_scores: dict[str, ModuleScore],
            findings: list[Finding]) -> EffortEstimate:
    bands = Counter(m.band for m in module_scores.values())
    base_days = sum(EFFORT_DAYS_PER_BAND[band] * n
                    for band, n in bands.items())

    multipliers: list[str] = []
    factor = 1.0
    tests = next((f for f in findings if f.rule_id == "DEBT-TESTS-001"), None)
    if tests is not None:
        m = (NO_TESTS_MULTIPLIER if tests.severity == Severity.HIGH
             else LOW_TESTS_MULTIPLIER)
        factor *= m
        multipliers.append(
            f"×{m} test-safety-net penalty (DEBT-TESTS-001 {tests.severity})")

    expected = base_days * factor * (1 + INTEGRATION_OVERHEAD)
    return EffortEstimate(
        expected_days=round(expected, 1),
        optimistic_days=round(expected * (1 - ESTIMATE_SPREAD), 1),
        pessimistic_days=round(expected * (1 + ESTIMATE_SPREAD), 1),
        band_counts=dict(bands),
        multipliers_applied=multipliers,
        assumptions=[
            f"Base effort per module: {EFFORT_DAYS_PER_BAND} person-days "
            "by complexity band",
            f"{int(INTEGRATION_OVERHEAD * 100)}% integration and "
            "verification overhead on top of module work",
            "One engineer per module; parallelism within a wave reduces "
            "calendar time, not person-days",
            "Estimates cover code migration only — infrastructure, data "
            "migration, and cutover are separate workstreams",
            f"±{int(ESTIMATE_SPREAD * 100)}% spread reflects model "
            "uncertainty; this is a heuristic model, not a quote",
        ],
    )


def _quick_wins(ctx: ProjectContext,
                module_scores: dict[str, ModuleScore],
                findings: list[Finding]) -> list[str]:
    arch: ArchitectureReport | None = ctx.get_artifact("architecture")
    if arch is None:
        return []
    hot_files = {
        e.file_path
        for f in findings
        if f.severity in (Severity.CRITICAL, Severity.HIGH)
        for e in f.evidence if e.file_path
    }
    wins = [
        path for path, score in module_scores.items()
        if score.band == "low"
        and path not in hot_files
        and arch.metrics.get(path) is not None
        and arch.metrics[path].wave == 0
        and PurePosixPath(path).name != "__init__.py"
    ]
    return sorted(wins)[:MAX_LISTED]


def _high_risk(module_scores: dict[str, ModuleScore],
               findings: list[Finding]) -> list[dict]:
    load = _findings_by_file(findings)
    risky = []
    for path, score in module_scores.items():
        reasons = []
        if score.band == "high":
            reasons.append(f"complexity {score.complexity}")
        if score.in_cycle:
            reasons.append("member of a dependency cycle")
        if load.get(path, 0) >= FINDING_NORM / 2:
            reasons.append("concentrated severe findings")
        if len(reasons) >= 2 or score.band == "high":
            risky.append({"path": path, "complexity": score.complexity,
                          "reasons": reasons})
    risky.sort(key=lambda r: -r["complexity"])
    return risky[:MAX_LISTED]


def compute_scorecard(
    findings: list[Finding], ctx: ProjectContext
) -> ScoreCard:
    """The single entry point: complete finding set in, ScoreCard out."""
    module_scores = _module_scores(ctx, findings)
    matrix, top_risks = _risk_matrix(findings)
    return ScoreCard(
        readiness=_readiness(findings),
        module_scores=module_scores,
        risk_matrix=matrix,
        top_risks=top_risks,
        quick_wins=_quick_wins(ctx, module_scores, findings),
        high_risk_modules=_high_risk(module_scores, findings),
        effort=_effort(module_scores, findings),
    )
