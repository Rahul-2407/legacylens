"""File-based JSON cache for external evidence responses.

Analysis is a batch workload: one project run can query hundreds of
packages, and re-runs should not re-hammer public APIs. A file cache keyed
by request identity, with TTL expiry, keeps runs fast, polite, and
reproducible within the TTL window. Corrupted or expired entries degrade
to a miss, never to an error.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class FileCache:
    def __init__(self, directory: Path, ttl_seconds: float) -> None:
        self._dir = directory
        self._ttl = ttl_seconds
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()[:32]
        return self._dir / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - entry["stored_at"] > self._ttl:
                path.unlink(missing_ok=True)
                return None
            return entry["value"]
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            path.unlink(missing_ok=True)   # corrupted entry -> miss
            return None

    def set(self, key: str, value: Any) -> None:
        payload = json.dumps({"stored_at": time.time(), "value": value})
        self._path(key).write_text(payload, encoding="utf-8")
