"""Configuration analyzer.

Two-tier detection policy, chosen to control false positives:

* key=value credential heuristic — ONLY in configuration-language files
  (.properties, .env, yaml, ini, json, xml), where `password=...` means a
  credential. In source code that pattern is usually `password = input()`.
* high-confidence patterns — in ALL text files, because they are
  near-unambiguous anywhere: AWS access key IDs, private-key PEM headers,
  and user:pass@ connection strings.

REDACTION INVARIANT: no finding ever contains a secret value. Snippets
mask everything past the first two characters. A secrets scanner that
copies secrets into its own report has exfiltrated them into every log,
export, and LLM prompt downstream.

Rules:
  CONF-SECRET-001  credential assigned in a config file        (HIGH)
  CONF-SECRET-002  high-confidence secret material             (CRITICAL)
  CONF-ENV-001     .env file committed to the repository       (MEDIUM)
"""

import re

from legacylens.analyzers.base import Analyzer, AnalyzerResult
from legacylens.analyzers.registry import registry
from legacylens.analyzers.util import read_text
from legacylens.domain.models import (
    Evidence,
    Finding,
    FindingCategory,
    ProjectContext,
    Severity,
)

_CONFIG_LANGUAGES = {"config", "properties", "yaml", "ini", "json", "xml"}
_TEMPLATE_MARKERS = ("example", "sample", "template", "dist")
MAX_EVIDENCE = 20

_KEY_VALUE = re.compile(
    r"(?i)^\s*(?:export\s+)?[\"']?(?P<key>[\w.\-]*"
    r"(?:password|passwd|pwd|secret|api[_-]?key|apikey|token|"
    r"access[_-]?key|private[_-]?key)[\w.\-]*)[\"']?\s*[:=]\s*"
    r"[\"']?(?P<value>[^\"'#\s][^\"'#]*)"
)
_PLACEHOLDER = re.compile(
    r"^(\$\{[^}]*\}|\{\{[^}]*\}\}|%\([^)]*\)s|\$\w+|<[^>]*>|"
    r"(?i:none|null|true|false|changeme|xxx+|todo))[,;]?$"
)
_HIGH_CONFIDENCE = (
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key material",
     re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("credentials embedded in URL",
     re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@")),
)


def redact(value: str) -> str:
    value = value.strip()
    return (value[:2] + "***") if len(value) > 2 else "***"


def _is_template(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    return any(marker in name for marker in _TEMPLATE_MARKERS)


@registry.register
class ConfigAnalyzer(Analyzer):
    id = "config_analysis"
    name = "Configuration analyzer"

    def analyze(self, ctx: ProjectContext) -> AnalyzerResult:
        config_hits: list[Evidence] = []
        high_hits: list[Evidence] = []
        env_files: list[str] = []

        for record in ctx.files:
            if _is_template(record.path):
                continue
            base = record.path.rsplit("/", 1)[-1]
            if base == ".env" or base.startswith(".env."):
                env_files.append(record.path)

            text = read_text(ctx, record)
            if text is None:
                continue

            for lineno, line in enumerate(text.splitlines(), start=1):
                for label, pattern in _HIGH_CONFIDENCE:
                    match = pattern.search(line)
                    if match:
                        high_hits.append(Evidence(
                            file_path=record.path,
                            line_start=lineno,
                            snippet=redact(match.group(0)),
                            detail=label,
                        ))
                        break
                else:
                    if record.language not in _CONFIG_LANGUAGES:
                        continue
                    match = _KEY_VALUE.match(line)
                    if not match:
                        continue
                    value = match.group("value").strip().rstrip(",;")
                    if len(value) < 4 or _PLACEHOLDER.match(value):
                        continue
                    config_hits.append(Evidence(
                        file_path=record.path,
                        line_start=lineno,
                        snippet=f"{match.group('key')}={redact(value)}",
                        detail="credential-like assignment",
                    ))

        findings: list[Finding] = []
        if high_hits:
            findings.append(self._secret_finding(
                "CONF-SECRET-002", Severity.CRITICAL, high_hits,
                title=f"{len(high_hits)} high-confidence secrets in the "
                      "repository",
                description=(
                    "Unambiguous secret material (cloud access keys, "
                    "private keys, or credentials embedded in connection "
                    "URLs) is committed to the repository. Anyone with "
                    "read access to this code — including every developer "
                    "who ever cloned it — holds these credentials. Rotate "
                    "them immediately and move them to a secret manager; "
                    "this blocks migration because repository access "
                    "typically widens during modernization programs."
                ),
            ))
        if config_hits:
            findings.append(self._secret_finding(
                "CONF-SECRET-001", Severity.HIGH, config_hits,
                title=f"{len(config_hits)} hardcoded credentials in "
                      "configuration files",
                description=(
                    "Configuration files assign literal values to "
                    "credential-named keys instead of referencing "
                    "environment variables or a secret manager. Values "
                    "are redacted in this report. Externalizing these is "
                    "a migration prerequisite: modern deployment targets "
                    "(containers, cloud) assume injected configuration."
                ),
            ))
        if env_files:
            findings.append(Finding(
                analyzer_id=self.id,
                rule_id="CONF-ENV-001",
                category=FindingCategory.CONFIGURATION,
                severity=Severity.MEDIUM,
                title=f"{len(env_files)} .env files committed to the "
                      "repository",
                description=(
                    ".env files typically hold environment-specific "
                    "secrets and should be gitignored; committed copies "
                    "leak credentials into history permanently (deleting "
                    "them later does not remove them from git history)."
                ),
                evidence=[Evidence(file_path=p) for p in env_files],
            ))
        return AnalyzerResult(findings=findings)

    def _secret_finding(self, rule_id, severity, hits, title,
                        description) -> Finding:
        return Finding(
            analyzer_id=self.id,
            rule_id=rule_id,
            category=FindingCategory.SECURITY,
            severity=severity,
            title=title,
            description=description,
            evidence=hits[:MAX_EVIDENCE],
            metadata={"count": len(hits),
                      "files": sorted({e.file_path for e in hits})},
        )
