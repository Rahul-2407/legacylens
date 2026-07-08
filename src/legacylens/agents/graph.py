"""LangGraph synthesis engine.

The state machine:

    analyst -> validate --retry--> analyst
                  |ok
                  v
             strategist -> validate --retry--> strategist
                              |ok
                              v
                           writer -> validate --retry--> writer
                                        |ok
                                        v
                                       END

Each validator re-runs the citation gate (agents/citation.py); on
violation it routes back to the agent with the violations injected into
the retry prompt. After max retries the section is recorded as failed and
the graph CONTINUES — one agent's failure must not cost the report its
other sections, the same partial-results philosophy as the pipeline
runner. LLM transport errors degrade the same way.
"""

import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from legacylens.agents.citation import (
    SectionedOutput,
    parse_output,
    validate_citations,
)
from legacylens.agents.digest import build_digest
from legacylens.agents.llm import LlmClient
from legacylens.agents.prompts import system_prompt, user_prompt
from legacylens.core.config import get_settings
from legacylens.core.exceptions import LlmError
from legacylens.core.logging import log_with_fields
from legacylens.domain.models import Finding, ProjectContext
from legacylens.scoring.engine import ScoreCard

logger = logging.getLogger(__name__)

AGENT_ORDER = ("analyst", "strategist", "writer")


class SynthesisState(TypedDict):
    digest: str
    known_ids: set[str]
    outputs: dict[str, Any]        # agent -> SectionedOutput (as dict)
    violations: dict[str, list[str]]
    attempts: dict[str, int]
    failures: dict[str, str]
    max_retries: int


class SynthesisResult(BaseModel):
    outputs: dict[str, SectionedOutput] = Field(default_factory=dict)
    failures: dict[str, str] = Field(default_factory=dict)
    attempts: dict[str, int] = Field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return not self.failures


class SynthesisEngine:
    def __init__(self, llm: LlmClient | None = None,
                 max_retries: int | None = None) -> None:
        if llm is None:
            from legacylens.agents.llm import GroqClient
            llm = GroqClient(get_settings())
        self._llm = llm
        self._max_retries = (max_retries if max_retries is not None
                             else get_settings().synthesis_max_retries)
        self._app = self._build_graph()

    # ------------------------------------------------------------- nodes

    def _agent_node(self, name: str):
        def run(state: SynthesisState) -> dict:
            prior = "\n\n".join(
                f"## {agent}\n" + "\n".join(
                    f"### {s['heading']}\n{s['content']}"
                    for s in state["outputs"][agent]["sections"]
                )
                for agent in AGENT_ORDER
                if agent in state["outputs"] and agent != name
            )
            attempts = dict(state["attempts"])
            attempts[name] = attempts.get(name, 0) + 1
            try:
                raw = self._llm.complete(
                    system_prompt(name),
                    user_prompt(name, state["digest"], prior,
                                state["violations"].get(name, [])),
                )
            except LlmError as exc:
                failures = dict(state["failures"])
                failures[name] = f"llm error: {exc}"
                return {"attempts": attempts, "failures": failures,
                        "violations": {**state["violations"], name: []}}

            parsed, error = parse_output(raw)
            if parsed is None:
                return {"attempts": attempts,
                        "violations": {**state["violations"],
                                       name: [error]}}
            violations = validate_citations(parsed, state["known_ids"])
            if violations:
                return {"attempts": attempts,
                        "violations": {**state["violations"],
                                       name: violations}}
            outputs = dict(state["outputs"])
            outputs[name] = parsed.model_dump()
            return {"attempts": attempts, "outputs": outputs,
                    "violations": {**state["violations"], name: []}}
        return run

    def _router(self, name: str, next_node: str):
        def route(state: SynthesisState) -> str:
            if name in state["outputs"] or name in state["failures"]:
                return "ok"
            if state["attempts"].get(name, 0) > state["max_retries"]:
                return "give_up"
            log_with_fields(
                logger, logging.WARNING, "synthesis retry",
                agent=name,
                attempt=state["attempts"].get(name, 0),
                violations=state["violations"].get(name, [])[:3],
            )
            return "retry"

        def give_up(state: SynthesisState) -> dict:
            failures = dict(state["failures"])
            failures[name] = (
                "citation validation failed after "
                f"{state['attempts'].get(name, 0)} attempts: "
                + "; ".join(state["violations"].get(name, [])[:3])
            )
            return {"failures": failures}

        return route, give_up

    def _build_graph(self):
        graph = StateGraph(SynthesisState)
        chain = list(AGENT_ORDER)
        for i, name in enumerate(chain):
            next_node = chain[i + 1] if i + 1 < len(chain) else END
            route, give_up = self._router(name, next_node)
            graph.add_node(name, self._agent_node(name))
            graph.add_node(f"{name}_give_up", give_up)
            graph.add_conditional_edges(
                name, route,
                {"ok": next_node, "retry": name,
                 "give_up": f"{name}_give_up"},
            )
            graph.add_edge(f"{name}_give_up", next_node)
        graph.set_entry_point(chain[0])
        return graph.compile()

    # --------------------------------------------------------------- api

    def run(self, findings: list[Finding], scorecard: ScoreCard,
            ctx: ProjectContext) -> SynthesisResult:
        digest = build_digest(findings, scorecard, ctx)
        state: SynthesisState = {
            "digest": digest,
            "known_ids": {f.finding_id for f in findings},
            "outputs": {},
            "violations": {},
            "attempts": {},
            "failures": {},
            "max_retries": self._max_retries,
        }
        final = self._app.invoke(
            state, config={"recursion_limit": 12 * len(AGENT_ORDER)})
        result = SynthesisResult(
            outputs={k: SectionedOutput.model_validate(v)
                     for k, v in final["outputs"].items()},
            failures=final["failures"],
            attempts=final["attempts"],
        )
        log_with_fields(
            logger, logging.INFO, "synthesis finished",
            succeeded=sorted(result.outputs),
            failed=sorted(result.failures),
            attempts=result.attempts,
        )
        return result
