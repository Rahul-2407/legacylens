"""LLM client: a one-method protocol and its Groq implementation.

The protocol is the seam that keeps the synthesis engine testable (tests
inject a scripted fake) and provider-agnostic (swapping Groq for the
Anthropic or OpenAI API is one new ~40-line class, zero engine changes).

The Groq implementation calls the OpenAI-compatible chat endpoint over
httpx directly — deliberately not through a heavyweight SDK; the surface
used is one POST.
"""

from typing import Any, Protocol

import httpx

from legacylens.core.config import Settings
from legacylens.core.exceptions import LlmError

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class LlmClient(Protocol):
    def complete(self, system: str, user: str) -> str:
        """Return the assistant message text for one exchange."""
        ...


class GroqClient:
    def __init__(self, settings: Settings,
                 transport: httpx.BaseTransport | None = None) -> None:
        if not settings.groq_api_key:
            raise LlmError(
                "LEGACYLENS_GROQ_API_KEY is not set; LLM synthesis "
                "requires it (deterministic analysis does not)"
            )
        self._settings = settings
        self._client = httpx.Client(
            timeout=60.0,
            transport=transport,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        )

    def complete(self, system: str, user: str) -> str:
        payload: dict[str, Any] = {
            "model": self._settings.groq_model,
            "temperature": self._settings.llm_temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._client.post(GROQ_URL, json=payload)
        except httpx.HTTPError as exc:
            raise LlmError(f"Groq request failed: {exc}") from exc
        if response.status_code >= 400:
            raise LlmError(
                f"Groq returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LlmError(f"Unexpected Groq response shape: {exc}") from exc
