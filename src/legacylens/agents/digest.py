"""Digest builder — the ONLY view of the project any LLM agent receives.

Agents never see raw code. They see this: findings with their IDs and key
evidence, the scorecard with its breakdowns, and the wave structure. That
containment is the platform's hallucination boundary — an agent cannot
invent facts about code it cannot see, and everything it can see carries
a citable ID.

Deterministic ordering (severity, then rule, then id) so identical runs
produce identical prompts — which makes agent behavior reproducible and
prompt-cache friendly.
"""

from legacylens.domain.models import Finding, ProjectContext, Severity
from legacylens.graph.algorithms import ArchitectureReport
from legacylens.scoring.engine import ScoreCard

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1,
                   Severity.MEDIUM: 2, Severity.LOW: 3, Severity.INFO: 4}
MAX_FINDINGS = 80
MAX_DESC = 260


def build_digest(findings: list[Finding], scorecard: ScoreCard,
                 ctx: ProjectContext) -> str:
    lines: list[str] = []
    r = scorecard.readiness
    lines.append(f"MIGRATION READINESS: {r.value}/100")
    for c in r.components:
        lines.append(f"  {c.delta:+d}: {c.detail}")

    e = scorecard.effort
    lines.append(
        f"EFFORT (heuristic): {e.expected_days} person-days expected "
        f"({e.optimistic_days}-{e.pessimistic_days}); "
        f"module bands: {e.band_counts}"
    )
    for assumption in e.assumptions:
        lines.append(f"  assumption: {assumption}")

    arch: ArchitectureReport | None = ctx.get_artifact("architecture")
    if arch:
        lines.append(
            f"MIGRATION WAVES: {len(arch.waves)} "
            f"(sizes {[len(w) for w in arch.waves]}); wave 0 has no "
            "internal dependencies and migrates first"
        )
        for i, wave in enumerate(arch.waves):
            sample = ", ".join(wave[:6]) + ("…" if len(wave) > 6 else "")
            lines.append(f"  wave {i}: {sample}")
        for cycle in arch.cycles:
            lines.append(f"  CYCLE (migrate as one unit): {', '.join(cycle)}")

    if scorecard.quick_wins:
        lines.append(f"QUICK WINS: {', '.join(scorecard.quick_wins)}")
    for m in scorecard.high_risk_modules:
        lines.append(
            f"HIGH-RISK MODULE: {m['path']} "
            f"(complexity {m['complexity']}; {'; '.join(m['reasons'])})"
        )

    lines.append(f"PROJECT: {len(ctx.files)} files, id {ctx.project_id}")
    lines.append("FINDINGS (cite by ID):")
    ordered = sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER[f.severity], f.rule_id, f.finding_id),
    )[:MAX_FINDINGS]
    for f in ordered:
        ev = next((e for e in f.evidence if e.file_path), f.evidence[0])
        where = (f"{ev.file_path}:{ev.line_start}" if ev.file_path
                 else (ev.reference_url or ev.detail or ""))
        desc = f.description[:MAX_DESC].replace("\n", " ")
        lines.append(
            f"[{f.finding_id}] {f.severity.upper()} {f.rule_id}: "
            f"{f.title} | evidence: {where} | {desc}"
        )
    if len(findings) > MAX_FINDINGS:
        lines.append(f"(+{len(findings) - MAX_FINDINGS} lower-severity "
                     "findings omitted from digest)")
    return "\n".join(lines)
