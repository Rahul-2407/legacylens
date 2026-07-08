"""End-of-life analyzer.

For every detected technology that maps to an endoflife.date product and
has a version, matches the version to its release cycle and emits:

  TECH-EOL-001  cycle is past end-of-life (HIGH; CRITICAL when EOL >= 3
                years ago — that long unsupported means unpatched CVEs
                have accumulated)
  TECH-EOL-002  cycle reaches end-of-life within 12 months (MEDIUM)
  EVID-UNAVAILABLE-001  endoflife.date unreachable; enrichment skipped

Every EOL finding carries dual evidence: the manifest/Dockerfile line the
version came from (project evidence) and the endoflife.date citation URL
(external authority). Claim -> observed version -> published EOL date:
the full chain a skeptical architect would demand.
"""

import logging
from datetime import date, timedelta

from legacylens.analyzers.base import Analyzer, AnalyzerResult
from legacylens.analyzers.registry import registry
from legacylens.analyzers.tech_detection import (
    DetectedTechnology,
    TechnologyProfile,
)
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
from legacylens.evidence.eol import EndOfLifeClient, ProductCycle

logger = logging.getLogger(__name__)

CRITICAL_YEARS_PAST_EOL = 3
APPROACHING_WINDOW_DAYS = 365


def match_cycle(
    version: str, cycles: list[ProductCycle]
) -> ProductCycle | None:
    """Longest cycle whose string prefixes the version on dot boundaries."""
    best: ProductCycle | None = None
    for cycle in cycles:
        c = cycle.cycle
        if version == c or version.startswith(c + "."):
            if best is None or len(c) > len(best.cycle):
                best = cycle
    return best


@registry.register
class EolAnalyzer(Analyzer):
    id = "tech_eol"
    name = "End-of-life analyzer"
    depends_on = ("tech_detection",)

    def __init__(self, client: EndOfLifeClient | None = None,
                 today: date | None = None) -> None:
        self._client = client or EndOfLifeClient(get_settings())
        self._today = today or date.today()

    def analyze(self, ctx: ProjectContext) -> AnalyzerResult:
        profile: TechnologyProfile | None = ctx.get_artifact("tech_detection")
        if profile is None:
            return AnalyzerResult()

        findings: list[Finding] = []
        unavailable: list[str] = []

        for tech in profile.with_eol_product():
            if not tech.version:
                continue
            try:
                cycles = self._client.get_cycles(tech.eol_product)
            except ExternalEvidenceError:
                unavailable.append(tech.eol_product)
                continue
            if not cycles:
                continue                     # product unknown to the source
            cycle = match_cycle(tech.version, cycles)
            if cycle is None:
                continue
            finding = self._judge(tech, cycle)
            if finding:
                findings.append(finding)

        if unavailable:
            findings.append(self._unavailable(sorted(set(unavailable))))
        return AnalyzerResult(findings=findings)

    def _judge(
        self, tech: DetectedTechnology, cycle: ProductCycle
    ) -> Finding | None:
        is_eol = cycle.is_eol(self._today)
        citation = Evidence(
            source=EvidenceSource.EXTERNAL_AUTHORITY,
            reference_url=EndOfLifeClient.product_url(tech.eol_product),
            detail=(
                f"{tech.name} cycle {cycle.cycle}: EOL "
                + (str(cycle.eol_date) if cycle.eol_date else "declared")
                + (f"; latest patch release {cycle.latest}"
                   if cycle.latest else "")
            ),
        )
        evidence = [*tech.evidence, citation]

        if is_eol:
            years_past = None
            severity = Severity.HIGH
            if cycle.eol_date:
                years_past = (self._today - cycle.eol_date).days / 365.25
                if years_past >= CRITICAL_YEARS_PAST_EOL:
                    severity = Severity.CRITICAL
            return Finding(
                analyzer_id=self.id,
                rule_id="TECH-EOL-001",
                category=FindingCategory.TECHNOLOGY,
                severity=severity,
                title=(
                    f"{tech.name} {tech.version} is past end-of-life"
                ),
                description=(
                    f"The project uses {tech.name} {tech.version}, whose "
                    f"release cycle ({cycle.cycle}) "
                    + (f"reached end-of-life on {cycle.eol_date}"
                       if cycle.eol_date else "is declared end-of-life")
                    + ". No security patches are being published for it; "
                    "every vulnerability disclosed since then is "
                    "permanently unpatched in this deployment. This is a "
                    "primary modernization driver."
                ),
                evidence=evidence,
                metadata={
                    "product": tech.eol_product,
                    "cycle": cycle.cycle,
                    "eol_date": str(cycle.eol_date) if cycle.eol_date else None,
                    "years_past_eol": round(years_past, 1)
                    if years_past is not None else None,
                },
            )

        if is_eol is False and cycle.eol_date and (
            cycle.eol_date - self._today
        ) <= timedelta(days=APPROACHING_WINDOW_DAYS):
            return Finding(
                analyzer_id=self.id,
                rule_id="TECH-EOL-002",
                category=FindingCategory.TECHNOLOGY,
                severity=Severity.MEDIUM,
                title=(
                    f"{tech.name} {tech.version} reaches end-of-life "
                    f"on {cycle.eol_date}"
                ),
                description=(
                    f"{tech.name} cycle {cycle.cycle} is still supported "
                    f"but reaches end-of-life on {cycle.eol_date} — within "
                    "the typical planning horizon of a migration program. "
                    "The migration plan should sequence its upgrade before "
                    "that date."
                ),
                evidence=evidence,
                metadata={"product": tech.eol_product, "cycle": cycle.cycle,
                          "eol_date": str(cycle.eol_date)},
            )
        return None

    def _unavailable(self, products: list[str]) -> Finding:
        return Finding(
            analyzer_id=self.id,
            rule_id="EVID-UNAVAILABLE-001",
            category=FindingCategory.DOCUMENTATION,
            severity=Severity.INFO,
            title="End-of-life enrichment unavailable for this run",
            description=(
                "endoflife.date could not be reached, so EOL status was "
                f"not verified for: {', '.join(products)}. Findings in "
                "this report are therefore a lower bound; re-run with "
                "network access for complete coverage."
            ),
            evidence=[Evidence(
                source=EvidenceSource.EXTERNAL_AUTHORITY,
                detail=f"unreachable products: {', '.join(products)}",
            )],
            metadata={"products": products},
        )
