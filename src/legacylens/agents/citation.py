"""Structured output parsing and citation validation.

Every agent must return JSON:

    {"sections": [{"heading": str, "content": str,
                   "citations": ["F-...", ...]}, ...]}

The validator is the hallucination gate. It rejects an output when:
  * the JSON does not parse or match the schema
  * any section has an empty citations list (uncited claims)
  * any cited finding ID does not exist in the evidence store
  * any F-xxxx ID mentioned inline in prose does not exist (agents can't
    smuggle invented references past the structured check)

Violations are returned as human-readable strings that get appended to
the retry prompt — the agent is told exactly what it invented.
"""

import json
import re

from pydantic import BaseModel, Field, ValidationError

_FINDING_ID = re.compile(r"\bF-[0-9a-f]{6,}\b")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class Section(BaseModel):
    heading: str = Field(min_length=1)
    content: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)


class SectionedOutput(BaseModel):
    sections: list[Section] = Field(min_length=1)


def parse_output(raw: str) -> tuple[SectionedOutput | None, str | None]:
    """(parsed, error). Tolerates markdown code fences around the JSON."""
    cleaned = _FENCE.sub("", raw.strip()).strip()
    try:
        return SectionedOutput.model_validate(json.loads(cleaned)), None
    except json.JSONDecodeError as exc:
        return None, f"response is not valid JSON: {exc}"
    except ValidationError as exc:
        return None, f"response JSON does not match the schema: {exc}"


def validate_citations(
    output: SectionedOutput, known_ids: set[str]
) -> list[str]:
    """Return violations; empty list means the output passed the gate."""
    violations: list[str] = []
    for i, section in enumerate(output.sections):
        label = f"section {i + 1} ('{section.heading[:40]}')"
        if not section.citations:
            violations.append(
                f"{label} has no citations — every section must cite at "
                "least one finding ID from the digest"
            )
        for cited in section.citations:
            if cited not in known_ids:
                violations.append(
                    f"{label} cites '{cited}', which does not exist in "
                    "the evidence store — cite only IDs from the digest"
                )
        for inline in _FINDING_ID.findall(section.content):
            if inline not in known_ids:
                violations.append(
                    f"{label} mentions '{inline}' in its text, which does "
                    "not exist — do not invent finding IDs"
                )
    return violations
