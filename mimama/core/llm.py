"""Thin LLM client over the openai SDK.

Works against any OpenAI-protocol endpoint (``base_url`` + ``api_key``), e.g.
a litellm server. All LLM use in mimama goes through :class:`LLMClient` so
tests can inject a fake.
"""

from __future__ import annotations

from typing import Any, Protocol

from openai import OpenAI

from mimama.config import LLMConfig


class LLMError(Exception):
    """The LLM endpoint failed or returned an unusable response."""


class SupportsComplete(Protocol):
    """What the rest of the codebase needs from an LLM client (fakeable)."""

    def complete(self, system: str, user: str) -> str: ...


class LLMClient:
    """Synchronous chat-completion client."""

    def __init__(self, config: LLMConfig, http_client: Any | None = None) -> None:
        """``http_client`` lets tests pass an httpx.Client that respx can mock."""
        self._model = config.model
        self._client = OpenAI(
            base_url=config.base_url, api_key=config.api_key, http_client=http_client
        )

    def complete(self, system: str, user: str) -> str:
        """One chat completion; returns the assistant message text."""
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if not response.choices:
            raise LLMError("LLM returned no choices")
        content = response.choices[0].message.content
        if content is None:
            raise LLMError("LLM returned empty content")
        return content
