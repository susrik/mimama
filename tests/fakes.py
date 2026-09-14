"""Shared test fakes."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from mimama.core.llm import LLMCancelled, LLMError


def _noop(_chunk: str) -> None:
    return None


@dataclass
class FakeLLMClient:
    """Records prompts; returns queued responses (or a fixed one)."""

    responses: list[str] = field(default_factory=lambda: ["FAKE RESPONSE"])
    calls: list[tuple[str, str]] = field(default_factory=list)
    # Pause between emitted chunks, letting a test reliably cancel mid-stream
    # (the real LLMClient paces naturally over the network; the fake needs
    # a little help to make cancellation deterministic).
    chunk_delay: float = 0.0
    # If set, blocks indefinitely right after the first chunk is emitted,
    # until the test calls `.set()` on it -- for fully deterministic
    # (non-racy) mid-stream cancellation tests.
    pause_after_first_chunk: threading.Event | None = None

    def complete(self, system: str, user: str) -> str:
        return self.stream_complete(system, user)

    def stream_complete(
        self,
        system: str,
        user: str,
        on_chunk: Callable[[str], None] = _noop,
        cancel_event: threading.Event | None = None,
    ) -> str:
        self.calls.append((system, user))
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        words = response.split(" ")
        for i, word in enumerate(words):
            if cancel_event is not None and cancel_event.is_set():
                raise LLMCancelled("cancelled")
            chunk = word if i == 0 else f" {word}"
            on_chunk(chunk)
            if i == 0 and self.pause_after_first_chunk is not None:
                self.pause_after_first_chunk.wait()
            elif self.chunk_delay:
                threading.Event().wait(self.chunk_delay)
        return response


@dataclass
class ErrorLLMClient:
    """Always fails, simulating an unreachable/broken LLM endpoint."""

    message: str = "endpoint down"

    def complete(self, system: str, user: str) -> str:
        return self.stream_complete(system, user)

    def stream_complete(
        self,
        system: str,
        user: str,
        on_chunk: Callable[[str], None] = _noop,
        cancel_event: threading.Event | None = None,
    ) -> str:
        raise LLMError(self.message)
