"""Shared test fakes."""

from dataclasses import dataclass, field

from mimama.core.llm import LLMError


@dataclass
class FakeLLMClient:
    """Records prompts; returns queued responses (or a fixed one)."""

    responses: list[str] = field(default_factory=lambda: ["FAKE RESPONSE"])
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


@dataclass
class ErrorLLMClient:
    """Always fails, simulating an unreachable/broken LLM endpoint."""

    message: str = "endpoint down"

    def complete(self, system: str, user: str) -> str:
        raise LLMError(self.message)
