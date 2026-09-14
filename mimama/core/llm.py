"""Thin LLM client over the openai SDK.

Works against any OpenAI-protocol endpoint (``base_url`` + ``api_key``), e.g.
a litellm server. All LLM use in mimama goes through :class:`LLMClient` so
tests can inject a fake.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol

from openai import OpenAI

from mimama.config import LLMConfig


class LLMError(Exception):
    """The LLM endpoint failed or returned an unusable response."""


class LLMCancelled(LLMError):
    """A streaming completion was cancelled (its ``cancel_event`` was set)."""


def _noop(_chunk: str) -> None:
    return None


class SupportsComplete(Protocol):
    """What the rest of the codebase needs from an LLM client (fakeable)."""

    def complete(self, system: str, user: str) -> str: ...

    def stream_complete(
        self,
        system: str,
        user: str,
        on_chunk: Callable[[str], None] = _noop,
        cancel_event: threading.Event | None = None,
    ) -> str: ...


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
        return self.stream_complete(system, user)

    def stream_complete(
        self,
        system: str,
        user: str,
        on_chunk: Callable[[str], None] = _noop,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """One chat completion, streamed.

        ``on_chunk`` is called with each incremental piece of text as it
        arrives; the full assistant message text is returned at the end.
        If ``cancel_event`` is set at any point, the upstream connection is
        closed immediately and :class:`LLMCancelled` is raised.
        """
        stream = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            stream=True,
        )
        parts: list[str] = []
        try:
            for chunk in stream:
                if cancel_event is not None and cancel_event.is_set():
                    raise LLMCancelled("cancelled")
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    parts.append(delta)
                    on_chunk(delta)
        finally:
            stream.close()
        if not parts:
            raise LLMError("LLM returned empty content")
        return "".join(parts)
