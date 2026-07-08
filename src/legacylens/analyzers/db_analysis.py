"""Database analyzer.

Detects which database engines the project actually touches, from two
independent evidence sources (drivers declared in manifests, connection
strings in configuration), and measures raw-SQL coupling in source code —
the single best predictor of database-migration pain, because every
embedded SQL string is dialect-coupled and invisible to an ORM migration.

  DB-ENGINE-INV-001  engines detected (INFO, inventory)
  DB-RAWSQL-001      raw SQL embedded in source (MEDIUM, grouped)
"""

import re
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

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
from legacylens.parsing.manifests.models import DependencyInventory

_DRIVER_ENGINES: dict[str, str] = {
    # PyPI
    "psycopg2": "postgresql", "psycopg2-binary": "postgresql",
    "psycopg": "postgresql",
    "pymysql": "mysql", "mysqlclient": "mysql",
    "mysql-connector-python": "mysql",
    "cx-oracle": "oracle", "cx_oracle": "oracle", "oracledb": "oracle",
    "pymongo": "mongodb", "redis": "redis", "pyodbc": "sqlserver",
    # npm
    "pg": "postgresql", "mysql": "mysql", "mysql2": "mysql",
    "mongodb": "mongodb", "mongoose": "mongodb", "ioredis": "redis",
    # Maven (group:artifact)
    "org.postgresql:postgresql": "postgresql",
    "mysql:mysql-connector-java": "mysql",
    "com.mysql:mysql-connector-j": "mysql",
    "org.mongodb:mongodb-driver-sync": "mongodb",
    "redis.clients:jedis": "redis",
    "com.microsoft.sqlserver:mssql-jdbc": "sqlserver",
    "com.h2database:h2": "h2",
}

_CONN_STRING = re.compile(
    r"(?i)\b(?:jdbc:(?P<jdbc>oracle|mysql|postgresql|sqlserver|h2)"
    r"|(?P<scheme>postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://)"
)
_SCHEME_ENGINES = {"postgres": "postgresql", "postgresql": "postgresql",
                   "mysql": "mysql", "mongodb": "mongodb",
                   "mongodb+srv": "mongodb", "redis": "redis"}

# Uppercase keywords only, deliberately: embedded SQL is overwhelmingly
# uppercase by convention, while lowercase "select a widget from the menu"
# is English. Missing lowercase SQL is an accepted trade-off vs. flooding
# the report with prose false positives.
_RAW_SQL = re.compile(
    r"[\"'][^\"']*\b(SELECT\s+[\w*,.\s]+\s+FROM|INSERT\s+INTO|"
    r"UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b"
)
_CONFIG_LANGUAGES = {"config", "properties", "yaml", "ini", "json", "xml"}
_SOURCE_LANGUAGES = {"python", "java", "javascript", "typescript"}
MAX_EVIDENCE = 20


class DetectedDatabase(BaseModel):
    model_config = ConfigDict(frozen=True)
    engine: str
    evidence: tuple[Evidence, ...] = ()


class DatabaseProfile(BaseModel):
    engines: list[DetectedDatabase] = Field(default_factory=list)
    raw_sql_by_file: dict[str, int] = Field(default_factory=dict)


@registry.register
class DatabaseAnalyzer(Analyzer):
    id = "db_analysis"
    name = "Database analyzer"
    depends_on = ("manifest_deps",)

    def analyze(self, ctx: ProjectContext) -> AnalyzerResult:
        engine_evidence: dict[str, list[Evidence]] = {}

        inventory: DependencyInventory | None = ctx.get_artifact(
            "manifest_deps")
        for dep in (inventory.dependencies if inventory else []):
            engine = _DRIVER_ENGINES.get(dep.name)
            if engine:
                engine_evidence.setdefault(engine, []).append(Evidence(
                    source=EvidenceSource.MANIFEST,
                    file_path=dep.manifest_path,
                    line_start=dep.line,
                    snippet=dep.raw_spec,
                    detail=f"database driver for {engine}",
                ))

        sql_counts: Counter[str] = Counter()
        sql_samples: list[Evidence] = []

        for record in ctx.files:
            text = None
            if record.language in _CONFIG_LANGUAGES:
                text = read_text(ctx, record)
                if text:
                    for lineno, line in enumerate(text.splitlines(), 1):
                        match = _CONN_STRING.search(line)
                        if not match:
                            continue
                        engine = (match.group("jdbc")
                                  or _SCHEME_ENGINES.get(
                                      match.group("scheme").lower()))
                        if engine:
                            engine_evidence.setdefault(engine, []).append(
                                Evidence(
                                    file_path=record.path,
                                    line_start=lineno,
                                    detail=f"connection string ({engine})",
                                ))
            elif record.language in _SOURCE_LANGUAGES:
                text = read_text(ctx, record)
                if text:
                    for lineno, line in enumerate(text.splitlines(), 1):
                        if _RAW_SQL.search(line):
                            sql_counts[record.path] += 1
                            if len(sql_samples) < MAX_EVIDENCE:
                                sql_samples.append(Evidence(
                                    file_path=record.path,
                                    line_start=lineno,
                                    snippet=line.strip()[:200],
                                ))

        profile = DatabaseProfile(
            engines=[
                DetectedDatabase(engine=e, evidence=tuple(ev[:5]))
                for e, ev in sorted(engine_evidence.items())
            ],
            raw_sql_by_file=dict(sql_counts),
        )

        findings: list[Finding] = []
        if profile.engines:
            findings.append(Finding(
                analyzer_id=self.id,
                rule_id="DB-ENGINE-INV-001",
                category=FindingCategory.DATABASE,
                severity=Severity.INFO,
                title="Database engines detected: "
                      + ", ".join(d.engine for d in profile.engines),
                description=(
                    "Engines identified from declared drivers and "
                    "configuration connection strings. Each engine is a "
                    "migration workstream with its own dialect, tooling, "
                    "and cutover plan."
                ),
                evidence=[ev for d in profile.engines
                          for ev in d.evidence][:MAX_EVIDENCE],
                metadata={"engines": [d.engine for d in profile.engines]},
            ))
        if sql_counts:
            total = sum(sql_counts.values())
            findings.append(Finding(
                analyzer_id=self.id,
                rule_id="DB-RAWSQL-001",
                category=FindingCategory.DATABASE,
                severity=Severity.MEDIUM,
                title=(
                    f"{total} raw SQL statements embedded in "
                    f"{len(sql_counts)} source files"
                ),
                description=(
                    "SQL strings written directly in application code are "
                    "coupled to the current engine's dialect and bypass "
                    "any ORM abstraction. Each is a manual review item if "
                    "the database engine or schema changes; the per-file "
                    "counts below locate the concentration."
                ),
                evidence=sql_samples,
                metadata={"total": total,
                          "by_file": dict(sql_counts.most_common())},
            ))
        return AnalyzerResult(findings=findings, artifact=profile)
