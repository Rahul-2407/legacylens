"""endoflife.date client.

GET https://endoflife.date/api/{product}.json returns release cycles:

    [{"cycle": "4.3", "eol": "2020-12-31", "latest": "4.3.30", ...}, ...]

The 'eol' field is polymorphic — a date string, or boolean true (EOL,
date unpublished), or false (supported) — and ProductCycle normalizes all
three honestly. Product-name mapping (e.g. Spring artifacts ->
'spring-framework') is deliberately NOT here: that's analyzer knowledge
(Module 6); this client speaks only endoflife.date's own vocabulary.
"""

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

from legacylens.core.config import Settings
from legacylens.evidence.cache import FileCache
from legacylens.evidence.http import EvidenceHttpClient

BASE_URL = "https://endoflife.date/api"


class ProductCycle(BaseModel):
    model_config = ConfigDict(frozen=True)

    cycle: str
    latest: str | None = None
    eol_date: date | None = None      # known EOL date
    eol_flag: bool | None = None      # true/false when no date published
    release_date: date | None = None

    def is_eol(self, today: date | None = None) -> bool | None:
        """True/False when determinable, None when the source doesn't say."""
        today = today or date.today()
        if self.eol_date is not None:
            return self.eol_date <= today
        return self.eol_flag

    @property
    def reference_url_fragment(self) -> str:
        return self.cycle


def _parse_cycle(raw: dict[str, Any]) -> ProductCycle:
    eol = raw.get("eol")
    eol_date, eol_flag = None, None
    if isinstance(eol, str):
        eol_date = date.fromisoformat(eol)
    elif isinstance(eol, bool):
        eol_flag = eol
    release = raw.get("releaseDate")
    return ProductCycle(
        cycle=str(raw.get("cycle", "")),
        latest=raw.get("latest"),
        eol_date=eol_date,
        eol_flag=eol_flag,
        release_date=date.fromisoformat(release) if isinstance(release, str)
        else None,
    )


class EndOfLifeClient:
    def __init__(
        self,
        settings: Settings,
        http: EvidenceHttpClient | None = None,
        cache: FileCache | None = None,
    ) -> None:
        self._http = http or EvidenceHttpClient(settings)
        self._cache = cache or FileCache(
            settings.cache_dir / "eol",
            settings.evidence_cache_ttl_hours * 3600,
        )

    def get_cycles(self, product: str) -> list[ProductCycle] | None:
        """All release cycles for a product, or None if unknown product."""
        key = f"eol:{product}"
        cached = self._cache.get(key)
        if cached is not None:
            payload = cached
        else:
            payload = self._http.request_json(
                "GET", f"{BASE_URL}/{product}.json"
            )
            if payload is None:
                self._cache.set(key, {"__not_found__": True})
                return None
            self._cache.set(key, payload)

        if isinstance(payload, dict) and payload.get("__not_found__"):
            return None
        return [_parse_cycle(item) for item in payload]

    @staticmethod
    def product_url(product: str) -> str:
        return f"https://endoflife.date/{product}"
