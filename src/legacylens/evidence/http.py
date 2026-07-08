"""Resilient HTTP layer for evidence clients.

Policy in one place so both clients behave identically:

* hard timeout per request (a hung API must not hang an analysis job)
* bounded retries with linear backoff for transient failures (network
  errors, 5xx, 429)
* 404 means "this product/package is unknown to the source" — a valid
  answer, returned as None, never an error
* any other failure after retries raises ExternalEvidenceError
* offline_mode short-circuits before any socket is opened

The httpx transport is injectable, so tests exercise the full retry and
parsing logic against httpx.MockTransport without touching the network.
"""

import logging
import time
from typing import Any

import httpx

from legacylens.core.config import Settings
from legacylens.core.exceptions import ExternalEvidenceError
from legacylens.core.logging import log_with_fields

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class EvidenceHttpClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        backoff_seconds: float = 0.5,
    ) -> None:
        self._settings = settings
        self._backoff = backoff_seconds
        self._client = httpx.Client(
            timeout=settings.http_timeout_seconds,
            transport=transport,
            headers={"User-Agent": "LegacyLens/0.1 (migration-analysis)"},
        )

    def request_json(
        self, method: str, url: str, json_body: Any | None = None
    ) -> Any | None:
        """Return parsed JSON, None for 404, or raise ExternalEvidenceError."""
        if self._settings.offline_mode:
            raise ExternalEvidenceError(
                f"offline_mode is enabled; refusing request to {url}"
            )

        attempts = self._settings.http_max_retries + 1
        last_error: str = "unknown"
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.request(method, url, json=json_body)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 404:
                    return None
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise ExternalEvidenceError(
                            f"Non-JSON response from {url}: {exc}"
                        ) from exc
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in _RETRYABLE_STATUS:
                    break
            if attempt < attempts:
                log_with_fields(
                    logger, logging.WARNING, "evidence request retry",
                    url=url, attempt=attempt, error=last_error,
                )
                time.sleep(self._backoff * attempt)

        raise ExternalEvidenceError(
            f"Request to {url} failed after {attempts} attempts: {last_error}"
        )

    def close(self) -> None:
        self._client.close()
