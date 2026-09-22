"""Contract tests for mimama.core.llm using respx to mock the HTTP layer."""

import json
import threading

import httpx
import pytest
import respx

from mimama.config import LLMConfig
from mimama.core.llm import LLMCancelled, LLMClient, LLMError

BASE_URL = "http://llm.test/v1"
CONFIG = LLMConfig(base_url=BASE_URL, api_key="sk-test", model="test-model")


def make_client() -> LLMClient:
    # A plain httpx.Client is injected so respx can intercept requests
    # (the openai SDK otherwise uses a vendored httpx respx cannot see).
    return LLMClient(CONFIG, http_client=httpx.Client())


def _chunk(content: str | None, finish_reason: str | None = None) -> dict[str, object]:
    delta: dict[str, object] = {} if content is None else {"content": content}
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def sse_response(chunks: list[dict[str, object]]) -> httpx.Response:
    """Build a text/event-stream response body from chat-completion-chunk dicts."""
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    body += "data: [DONE]\n\n"
    return httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"}
    )


@respx.mock
def test_complete_sends_expected_request() -> None:
    route = respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=sse_response([_chunk("hel"), _chunk("lo"), _chunk(None, "stop")])
    )
    result = make_client().complete(system="SYS", user="USR")
    assert result == "hello"
    request = route.calls.last.request
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["stream"] is True
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]
    assert request.headers["authorization"] == "Bearer sk-test"


@respx.mock
def test_empty_content_raises() -> None:
    respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=sse_response([_chunk(None, "stop")])
    )
    with pytest.raises(LLMError, match="empty content"):
        make_client().complete(system="s", user="u")


@respx.mock
def test_no_choices_raises() -> None:
    payload = _chunk("x")
    payload["choices"] = []
    respx.post(f"{BASE_URL}/chat/completions").mock(return_value=sse_response([payload]))
    with pytest.raises(LLMError, match="empty content"):
        make_client().complete(system="s", user="u")


@respx.mock
def test_stream_complete_calls_on_chunk_incrementally() -> None:
    respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=sse_response([_chunk("foo"), _chunk("bar"), _chunk(None, "stop")])
    )
    seen: list[str] = []
    result = make_client().stream_complete(system="s", user="u", on_chunk=seen.append)
    assert seen == ["foo", "bar"]
    assert result == "foobar"


@respx.mock
def test_stream_complete_cancelled_raises_and_stops_early() -> None:
    respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=sse_response(
            [_chunk("a"), _chunk("b"), _chunk("c"), _chunk(None, "stop")]
        )
    )
    cancel_event = threading.Event()
    seen: list[str] = []

    def on_chunk(text: str) -> None:
        seen.append(text)
        if text == "a":
            cancel_event.set()

    with pytest.raises(LLMCancelled):
        make_client().stream_complete(
            system="s", user="u", on_chunk=on_chunk, cancel_event=cancel_event
        )
    # cancellation is checked before processing the *next* chunk, so "a" was
    # already delivered but "b"/"c" were not.
    assert seen == ["a"]
