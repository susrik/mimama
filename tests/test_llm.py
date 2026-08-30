"""Contract tests for mimama.core.llm using respx to mock the HTTP layer."""

import httpx
import pytest
import respx

from mimama.config import LLMConfig
from mimama.core.llm import LLMClient, LLMError

BASE_URL = "http://llm.test/v1"
CONFIG = LLMConfig(base_url=BASE_URL, api_key="sk-test", model="test-model")


def make_client() -> LLMClient:
    # A plain httpx.Client is injected so respx can intercept requests
    # (the openai SDK otherwise uses a vendored httpx respx cannot see).
    return LLMClient(CONFIG, http_client=httpx.Client())


def completion_json(content: str | None) -> dict[str, object]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


@respx.mock
def test_complete_sends_expected_request() -> None:
    route = respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_json("hello"))
    )
    result = make_client().complete(system="SYS", user="USR")
    assert result == "hello"
    request = route.calls.last.request
    import json

    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]
    assert request.headers["authorization"] == "Bearer sk-test"


@respx.mock
def test_empty_content_raises() -> None:
    respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_json(None))
    )
    with pytest.raises(LLMError, match="empty content"):
        make_client().complete(system="s", user="u")


@respx.mock
def test_no_choices_raises() -> None:
    payload = completion_json("x")
    payload["choices"] = []
    respx.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(200, json=payload)
    )
    with pytest.raises(LLMError, match="no choices"):
        make_client().complete(system="s", user="u")
