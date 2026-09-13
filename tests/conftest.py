"""Shared fixtures: a populated notes tree + a test app/client with a fake LLM."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mimama.config import Config
from mimama.server.app import create_app
from tests.fakes import FakeLLMClient


@pytest.fixture()
def notes_root(tmp_path: Path) -> Path:
    root = tmp_path / "notes"
    eurohpc = root / "projects" / "eurohpc"
    eurohpc.mkdir(parents=True)
    (root / "AGENTS.md").write_text("ROOT CONTEXT", encoding="utf-8")
    (root / "projects" / "AGENTS.md").write_text("PROJECTS CONTEXT", encoding="utf-8")
    (eurohpc / "2026-08-29-sync.md").write_text(
        "EuroHPC sync. Erik presented the plan.", encoding="utf-8"
    )
    (eurohpc / "2026-08-01-kickoff.md").write_text(
        "EuroHPC kickoff. We decided to use litellm.", encoding="utf-8"
    )
    (root / "todo.md").write_text("buy milk", encoding="utf-8")
    return root


@pytest.fixture()
def config(notes_root: Path) -> Config:
    return Config.model_validate(
        {
            "notes": {"root": str(notes_root), "git": False},
            "llm": {
                "base_url": "http://llm.test/v1",
                "api_key": "sk-test",
                "model": "test-model",
            },
            "transcripts": {
                "whisper": {"model": "/fake/models/ggml-test.bin"},
            },
            "user": {"name": "Erik Reid", "aliases": ["Erik"]},
        }
    )


@pytest.fixture()
def fake_llm() -> FakeLLMClient:
    return FakeLLMClient()


@pytest.fixture()
def app(config: Config, fake_llm: FakeLLMClient) -> FastAPI:
    return create_app(config=config, llm=fake_llm)


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
