"""Synthesis engine tests. The centerpiece: a scripted LLM whose first
answer cites a hallucinated finding ID — proving the validator rejects
it, the retry prompt names the exact violation, and the corrected second
answer passes."""

import json
from pathlib import Path

import httpx
import pytest

from legacylens.agents.citation import parse_output, validate_citations
from legacylens.agents.digest import build_digest
from legacylens.agents.graph import SynthesisEngine
from legacylens.agents.llm import GroqClient
from legacylens.core.config import Settings
from legacylens.core.exceptions import LlmError
from legacylens.domain.models import (
    Evidence,
    Finding,
    FindingCategory,
    ProjectContext,
    Severity,
)
from legacylens.scoring.engine import compute_scorecard


def finding(rule="R-1", severity=Severity.HIGH) -> Finding:
    return Finding(
        analyzer_id="x", rule_id=rule, category=FindingCategory.TECHNOLOGY,
        severity=severity, title=f"{rule} title", description="desc",
        evidence=[Evidence(file_path="src/a.py", line_start=3)],
    )


def sections(*citation_lists, content="Grounded claim.") -> str:
    return json.dumps({"sections": [
        {"heading": f"S{i}", "content": content, "citations": list(c)}
        for i, c in enumerate(citation_lists)
    ]})


class ScriptedLlm:
    """Returns queued responses; records every prompt it was given."""

    def __init__(self, responses: list[str]):
        self._queue = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self._queue:
            raise AssertionError("LLM called more times than scripted")
        return self._queue.pop(0)


class FailingLlm:
    def complete(self, system, user):
        raise LlmError("provider down")


@pytest.fixture()
def scored(tmp_path):
    findings = [finding("R-1"), finding("R-2", Severity.CRITICAL)]
    ctx = ProjectContext(project_id="p1", root=tmp_path)
    return findings, compute_scorecard(findings, ctx), ctx


class TestCitationGate:
    def test_valid_output_passes(self):
        out, err = parse_output(sections(["F-abc123"]))
        assert err is None
        assert validate_citations(out, {"F-abc123"}) == []

    def test_fenced_json_tolerated(self):
        out, err = parse_output("```json\n" + sections(["F-abc123"]) + "\n```")
        assert err is None and out is not None

    def test_unknown_citation_rejected(self):
        out, _ = parse_output(sections(["F-deadbeef9999"]))
        violations = validate_citations(out, {"F-abc123"})
        assert any("does not exist" in v for v in violations)

    def test_empty_citations_rejected(self):
        out, _ = parse_output(sections([]))
        assert any("no citations" in v
                   for v in validate_citations(out, {"F-abc123"}))

    def test_inline_hallucinated_id_caught(self):
        raw = json.dumps({"sections": [{
            "heading": "S", "citations": ["F-abc123"],
            "content": "See F-abc123 and also F-ffffff999999.",
        }]})
        out, _ = parse_output(raw)
        violations = validate_citations(out, {"F-abc123"})
        assert any("F-ffffff999999" in v and "invent" in v
                   for v in violations)

    def test_garbage_is_a_parse_error(self):
        out, err = parse_output("Sure! Here's my analysis...")
        assert out is None and "not valid JSON" in err


class TestDigest:
    def test_digest_contains_ids_scores_and_is_deterministic(self, scored):
        findings, card, ctx = scored
        digest = build_digest(findings, card, ctx)
        for f in findings:
            assert f.finding_id in digest
        assert f"MIGRATION READINESS: {card.readiness.value}/100" in digest
        assert "heuristic model" in digest       # assumptions travel along
        assert digest == build_digest(findings, card, ctx)

    def test_critical_findings_listed_first(self, scored):
        findings, card, ctx = scored
        digest = build_digest(findings, card, ctx)
        assert digest.index("R-2") < digest.index("R-1")


class TestSynthesisEngine:
    def test_happy_path_runs_all_three_agents(self, scored):
        findings, card, ctx = scored
        fid = findings[0].finding_id
        llm = ScriptedLlm([sections([fid])] * 3)
        result = SynthesisEngine(llm=llm, max_retries=2).run(
            findings, card, ctx)

        assert result.succeeded
        assert set(result.outputs) == {"analyst", "strategist", "writer"}
        assert result.attempts == {"analyst": 1, "strategist": 1,
                                   "writer": 1}
        # strategist and writer receive prior agents' sections
        assert "OUTPUT OF PRIOR AGENTS" in llm.calls[1][1]
        assert "FINDINGS DIGEST" in llm.calls[0][1]

    def test_hallucinated_citation_triggers_retry_with_feedback(self, scored):
        findings, card, ctx = scored
        fid = findings[0].finding_id
        llm = ScriptedLlm([
            sections(["F-badbadbadbad"]),      # analyst attempt 1: rejected
            sections([fid]),                   # analyst attempt 2: passes
            sections([fid]),                   # strategist
            sections([fid]),                   # writer
        ])
        result = SynthesisEngine(llm=llm, max_retries=2).run(
            findings, card, ctx)

        assert result.succeeded
        assert result.attempts["analyst"] == 2
        retry_prompt = llm.calls[1][1]
        assert "REJECTED" in retry_prompt
        assert "F-badbadbadbad" in retry_prompt   # told exactly what it invented

    def test_persistent_failure_degrades_gracefully(self, scored):
        findings, card, ctx = scored
        fid = findings[0].finding_id
        llm = ScriptedLlm([
            sections([fid]),                    # analyst ok
            sections(["F-000000000000"]),       # strategist: 3 bad attempts
            sections(["F-000000000000"]),
            sections(["F-000000000000"]),
            sections([fid]),                    # writer still runs
        ])
        result = SynthesisEngine(llm=llm, max_retries=2).run(
            findings, card, ctx)

        assert set(result.outputs) == {"analyst", "writer"}
        assert "strategist" in result.failures
        assert "citation validation failed after 3 attempts" in \
            result.failures["strategist"]

    def test_llm_transport_error_degrades_gracefully(self, scored):
        findings, card, ctx = scored
        result = SynthesisEngine(llm=FailingLlm(), max_retries=2).run(
            findings, card, ctx)
        assert result.outputs == {}
        assert set(result.failures) == {"analyst", "strategist", "writer"}
        assert "provider down" in result.failures["analyst"]


class TestGroqClient:
    def make(self, handler):
        settings = Settings(_env_file=None, groq_api_key="gsk_test")
        return GroqClient(settings, transport=httpx.MockTransport(handler))

    def test_request_shape_and_parse(self):
        captured = {}

        def handler(request):
            captured["auth"] = request.headers["authorization"]
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "choices": [{"message": {"content": '{"sections": []}'}}],
            })

        client = self.make(handler)
        text = client.complete("sys", "user")
        assert text == '{"sections": []}'
        assert captured["auth"] == "Bearer gsk_test"
        body = captured["body"]
        assert body["model"] == "llama-3.3-70b-versatile"
        assert body["response_format"] == {"type": "json_object"}
        assert [m["role"] for m in body["messages"]] == ["system", "user"]

    def test_http_error_raises_llm_error(self):
        client = self.make(lambda r: httpx.Response(500, text="boom"))
        with pytest.raises(LlmError, match="HTTP 500"):
            client.complete("s", "u")

    def test_missing_key_fails_fast(self):
        with pytest.raises(LlmError, match="GROQ_API_KEY"):
            GroqClient(Settings(_env_file=None, groq_api_key=None))
