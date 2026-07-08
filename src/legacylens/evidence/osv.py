"""OSV.dev vulnerability client.

POST https://api.osv.dev/v1/query with {package: {name, ecosystem},
version} returns known vulnerabilities affecting that exact version.
POST /v1/querybatch answers many packages cheaply (ids only) — the right
call shape for a 300-dependency legacy project; details can then be
fetched per-id for the packages that actually matter.

Severity honesty: OSV publishes CVSS vectors and sometimes a label; this
client surfaces exactly what the source provides (label and/or vector)
and computes nothing itself.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from legacylens.core.config import Settings
from legacylens.evidence.cache import FileCache
from legacylens.evidence.http import EvidenceHttpClient
from legacylens.parsing.manifests.models import Ecosystem

BASE_URL = "https://api.osv.dev/v1"

_ECOSYSTEM_NAMES: dict[Ecosystem, str] = {
    Ecosystem.PYPI: "PyPI",
    Ecosystem.NPM: "npm",
    Ecosystem.MAVEN: "Maven",
}


class Vulnerability(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    summary: str | None = None
    aliases: tuple[str, ...] = ()
    severity_label: str | None = None   # e.g. "HIGH" when published
    cvss_vector: str | None = None

    @property
    def reference_url(self) -> str:
        return f"https://osv.dev/vulnerability/{self.id}"


def _parse_vuln(raw: dict[str, Any]) -> Vulnerability:
    label = (raw.get("database_specific") or {}).get("severity")
    vector = None
    for entry in raw.get("severity", []) or []:
        if str(entry.get("type", "")).startswith("CVSS"):
            vector = entry.get("score")
            break
    return Vulnerability(
        id=raw["id"],
        summary=raw.get("summary"),
        aliases=tuple(raw.get("aliases", []) or []),
        severity_label=label,
        cvss_vector=vector,
    )


class OsvClient:
    def __init__(
        self,
        settings: Settings,
        http: EvidenceHttpClient | None = None,
        cache: FileCache | None = None,
    ) -> None:
        self._http = http or EvidenceHttpClient(settings)
        self._cache = cache or FileCache(
            settings.cache_dir / "osv",
            settings.evidence_cache_ttl_hours * 3600,
        )

    def query(
        self, name: str, ecosystem: Ecosystem, version: str
    ) -> list[Vulnerability]:
        """Known vulnerabilities affecting an exact package version."""
        key = f"osv:{ecosystem}:{name}:{version}"
        payload = self._cache.get(key)
        if payload is None:
            payload = self._http.request_json("POST", f"{BASE_URL}/query", {
                "package": {
                    "name": name,
                    "ecosystem": _ECOSYSTEM_NAMES[ecosystem],
                },
                "version": version,
            }) or {}
            self._cache.set(key, payload)
        return [_parse_vuln(v) for v in payload.get("vulns", []) or []]

    def query_batch(
        self, packages: list[tuple[str, Ecosystem, str]]
    ) -> list[list[str]]:
        """Vulnerability IDs per (name, ecosystem, version), order-aligned.

        Not cached: the batch endpoint is itself the cheap path, and
        per-package caching happens in query() when details are fetched.
        """
        if not packages:
            return []
        payload = self._http.request_json(
            "POST", f"{BASE_URL}/querybatch",
            {"queries": [
                {"package": {"name": name,
                             "ecosystem": _ECOSYSTEM_NAMES[eco]},
                 "version": version}
                for name, eco, version in packages
            ]},
        ) or {}
        results = payload.get("results", []) or []
        return [
            [v["id"] for v in (entry.get("vulns") or [])]
            for entry in results
        ]
