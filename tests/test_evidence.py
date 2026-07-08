"""Evidence client tests — MockTransport fixtures modeled on real API
responses; no network access, ever, in unit tests."""

import json
import time
from datetime import date

import httpx
import pytest

from legacylens.core.config import Settings
from legacylens.core.exceptions import ExternalEvidenceError
from legacylens.evidence.cache import FileCache
from legacylens.evidence.eol import EndOfLifeClient
from legacylens.evidence.http import EvidenceHttpClient
from legacylens.evidence.osv import OsvClient
from legacylens.parsing.manifests.models import Ecosystem


def settings(tmp_path, **overrides) -> Settings:
    base = dict(cache_dir=tmp_path / "cache", http_max_retries=2)
    base.update(overrides)
    return Settings(_env_file=None, **base)


def client_with(tmp_path, handler, **overrides) -> EvidenceHttpClient:
    return EvidenceHttpClient(
        settings(tmp_path, **overrides),
        transport=httpx.MockTransport(handler),
        backoff_seconds=0.0,
    )


class TestFileCache:
    def test_roundtrip_and_expiry(self, tmp_path, monkeypatch):
        cache = FileCache(tmp_path, ttl_seconds=100)
        cache.set("k", {"a": 1})
        assert cache.get("k") == {"a": 1}

        real_time = time.time()
        monkeypatch.setattr(time, "time", lambda: real_time + 101)
        assert cache.get("k") is None  # expired

    def test_corrupted_entry_is_a_miss(self, tmp_path):
        cache = FileCache(tmp_path, ttl_seconds=100)
        cache.set("k", 1)
        next(tmp_path.glob("*.json")).write_text("{corrupted")
        assert cache.get("k") is None


class TestHttpPolicy:
    def test_retries_transient_failures_then_succeeds(self, tmp_path):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"ok": True})

        client = client_with(tmp_path, handler)
        assert client.request_json("GET", "https://x.test/a") == {"ok": True}
        assert calls["n"] == 3

    def test_exhausted_retries_raise(self, tmp_path):
        client = client_with(tmp_path, lambda r: httpx.Response(503))
        with pytest.raises(ExternalEvidenceError, match="after 3 attempts"):
            client.request_json("GET", "https://x.test/a")

    def test_non_retryable_status_fails_fast(self, tmp_path):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(400)

        client = client_with(tmp_path, handler)
        with pytest.raises(ExternalEvidenceError, match="HTTP 400"):
            client.request_json("GET", "https://x.test/a")
        assert calls["n"] == 1

    def test_404_is_a_valid_none_answer(self, tmp_path):
        client = client_with(tmp_path, lambda r: httpx.Response(404))
        assert client.request_json("GET", "https://x.test/nope") is None

    def test_offline_mode_never_opens_a_socket(self, tmp_path):
        def handler(request):  # pragma: no cover — must not be reached
            raise AssertionError("socket opened in offline mode")

        client = client_with(tmp_path, handler, offline_mode=True)
        with pytest.raises(ExternalEvidenceError, match="offline_mode"):
            client.request_json("GET", "https://x.test/a")


EOL_FIXTURE = [
    {"cycle": "5.3", "eol": "2024-08-31", "latest": "5.3.39",
     "releaseDate": "2020-10-27"},
    {"cycle": "6.1", "eol": False, "latest": "6.1.14"},
    {"cycle": "3.2", "eol": True},
]


class TestEndOfLifeClient:
    def make(self, tmp_path, handler):
        cfg = settings(tmp_path)
        return EndOfLifeClient(
            cfg,
            http=EvidenceHttpClient(cfg, transport=httpx.MockTransport(handler),
                                    backoff_seconds=0.0),
        )

    def test_cycle_parsing_and_eol_logic(self, tmp_path):
        client = self.make(
            tmp_path, lambda r: httpx.Response(200, json=EOL_FIXTURE))
        cycles = client.get_cycles("spring-framework")
        by_cycle = {c.cycle: c for c in cycles}

        today = date(2026, 7, 2)
        assert by_cycle["5.3"].eol_date == date(2024, 8, 31)
        assert by_cycle["5.3"].is_eol(today) is True
        assert by_cycle["6.1"].is_eol(today) is False
        assert by_cycle["3.2"].is_eol(today) is True  # flag, no date

    def test_unknown_product_is_none_and_cached(self, tmp_path):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(404)

        client = self.make(tmp_path, handler)
        assert client.get_cycles("no-such-product") is None
        assert client.get_cycles("no-such-product") is None
        assert calls["n"] == 1  # negative result cached too

    def test_cache_prevents_second_request(self, tmp_path):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json=EOL_FIXTURE)

        client = self.make(tmp_path, handler)
        client.get_cycles("python")
        client.get_cycles("python")
        assert calls["n"] == 1


OSV_FIXTURE = {"vulns": [{
    "id": "GHSA-xxxx-1234",
    "summary": "Remote code execution in template rendering",
    "aliases": ["CVE-2019-0001"],
    "database_specific": {"severity": "HIGH"},
    "severity": [{"type": "CVSS_V3",
                  "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
}]}


class TestOsvClient:
    def make(self, tmp_path, handler):
        cfg = settings(tmp_path)
        return OsvClient(
            cfg,
            http=EvidenceHttpClient(cfg, transport=httpx.MockTransport(handler),
                                    backoff_seconds=0.0),
        )

    def test_query_parses_vulnerability(self, tmp_path):
        captured = {}

        def handler(request):
            captured.update(json.loads(request.content))
            return httpx.Response(200, json=OSV_FIXTURE)

        client = self.make(tmp_path, handler)
        vulns = client.query("flask", Ecosystem.PYPI, "0.12.4")

        assert captured["package"] == {"name": "flask", "ecosystem": "PyPI"}
        assert captured["version"] == "0.12.4"
        vuln = vulns[0]
        assert vuln.id == "GHSA-xxxx-1234"
        assert vuln.severity_label == "HIGH"
        assert vuln.cvss_vector.startswith("CVSS:3.1")
        assert vuln.reference_url.endswith("GHSA-xxxx-1234")

    def test_clean_package_returns_empty(self, tmp_path):
        client = self.make(tmp_path, lambda r: httpx.Response(200, json={}))
        assert client.query("requests", Ecosystem.PYPI, "2.32.0") == []

    def test_batch_returns_order_aligned_ids(self, tmp_path):
        def handler(request):
            body = json.loads(request.content)
            assert len(body["queries"]) == 2
            assert body["queries"][1]["package"]["ecosystem"] == "Maven"
            return httpx.Response(200, json={"results": [
                {"vulns": [{"id": "V-1"}, {"id": "V-2"}]},
                {},
            ]})

        client = self.make(tmp_path, handler)
        ids = client.query_batch([
            ("flask", Ecosystem.PYPI, "0.12.4"),
            ("org.springframework:spring-core", Ecosystem.MAVEN, "4.3.9"),
        ])
        assert ids == [["V-1", "V-2"], []]

    def test_empty_batch_short_circuits(self, tmp_path):
        client = self.make(
            tmp_path,
            lambda r: (_ for _ in ()).throw(AssertionError("no request")))
        assert client.query_batch([]) == []
