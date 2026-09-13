"""Tests for the guided click CLI (in-process against the test app)."""

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner, Result
from fastapi.testclient import TestClient

import mimama.cli.main as cli_main
from mimama.api_client import APIClient
from tests.fakes import FakeLLMClient


@pytest.fixture()
def config_file(tmp_path: Path, notes_root: Path) -> Path:
    cfg = {
        "notes": {"root": str(notes_root), "git": False},
        "llm": {"base_url": "http://x/v1", "api_key": "k", "model": "m"},
        "transcripts": {"whisper": {"model": "/fake/models/ggml-test.bin"}},
    }
    path = tmp_path / "cli-config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


class _TestClientTransport(httpx.BaseTransport):
    """Sync httpx transport that forwards requests to a TestClient."""

    def __init__(self, test_client: TestClient) -> None:
        self._test_client = test_client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._test_client.request(
            request.method,
            str(request.url),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
        )


@pytest.fixture()
def run(
    client: TestClient, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> "type[_Runner]":
    """CLI runner whose APIClient talks to the in-process test app."""
    transport = _TestClientTransport(client)

    def make_client(server_url: str, timeout: float = 120.0) -> APIClient:
        return APIClient("http://testserver", transport=transport)

    monkeypatch.setattr(cli_main, "APIClient", make_client)

    class _Bound(_Runner):
        _config_file = config_file

    return _Bound


class _Runner:
    _config_file: Path

    @classmethod
    def cli(cls, *args: str, input: str | None = None) -> Result:
        return CliRunner().invoke(
            cli_main.cli,
            ["--config", str(cls._config_file), *args],
            input=input,
        )


class TestLs:
    def test_root(self, run: type[_Runner]) -> None:
        result = run.cli("ls")
        assert result.exit_code == 0
        assert "d projects" in result.output
        assert "- todo.md" in result.output

    def test_bad_path(self, run: type[_Runner]) -> None:
        result = run.cli("ls", "nope")
        assert result.exit_code != 0
        assert "not a directory" in result.output


class TestShow:
    def test_direct(self, run: type[_Runner]) -> None:
        result = run.cli("show", "todo.md")
        assert result.exit_code == 0
        assert "buy milk" in result.output

    def test_interactive_picker(self, run: type[_Runner]) -> None:
        # note list is walked depth-first; pick the last entry (todo.md)
        result = run.cli("show", input="5\n")
        assert result.exit_code == 0
        assert "Notes:" in result.output
        assert "buy milk" in result.output

    def test_missing(self, run: type[_Runner]) -> None:
        result = run.cli("show", "nope.md")
        assert result.exit_code != 0
        assert "no such note" in result.output


class TestSearch:
    def test_hits(self, run: type[_Runner]) -> None:
        result = run.cli("search", "eurohpc")
        assert result.exit_code == 0
        assert "2026-08-29-sync.md" in result.output

    def test_no_hits(self, run: type[_Runner]) -> None:
        result = run.cli("search", "zzzz")
        assert "(no matches)" in result.output


class TestQuery:
    def test_with_argument(self, run: type[_Runner], fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = [json.dumps({"keywords": ["eurohpc"]})]
        result = run.cli("query", "eurohpc notes")
        assert result.exit_code == 0
        assert "2026-08-29-sync.md" in result.output

    def test_prompted(self, run: type[_Runner], fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = [json.dumps({"keywords": ["zzzz"]})]
        result = run.cli("query", input="anything?\n")
        assert "Question" in result.output
        assert "(no matching notes)" in result.output

    def test_llm_failure(self, run: type[_Runner], fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = ["bad", "bad"]
        result = run.cli("query", "q")
        assert result.exit_code != 0
        assert "valid query plan" in result.output


class TestConvert:
    def test_fully_scripted(
        self, run: type[_Runner], fake_llm: FakeLLMClient, tmp_path: Path
    ) -> None:
        fake_llm.responses = ["# Minutes"]
        transcript = tmp_path / "t.txt"
        transcript.write_text("raw talk", encoding="utf-8")
        result = run.cli(
            "convert",
            "--folder", "projects/eurohpc",
            "--transcript-file", str(transcript),
            "--filename", "out.md",
            "--yes",
        )
        assert result.exit_code == 0
        assert "saved: projects/eurohpc/out.md" in result.output
        assert "# Minutes" in result.output

    def test_guided_folder_picker_and_confirm(
        self, run: type[_Runner], fake_llm: FakeLLMClient, tmp_path: Path
    ) -> None:
        fake_llm.responses = ["# Minutes"]
        transcript = tmp_path / "t.txt"
        transcript.write_text("raw talk", encoding="utf-8")
        # folder pick "3" (projects/eurohpc), source "file", path, confirm
        result = run.cli(
            "convert",
            input=f"3\nfile\n{transcript}\ny\n",
        )
        assert result.exit_code == 0
        assert "Folders:" in result.output
        assert "Send to LLM?" in result.output
        assert "saved: projects/eurohpc/" in result.output

    def test_declined_confirmation_aborts(
        self, run: type[_Runner], tmp_path: Path
    ) -> None:
        transcript = tmp_path / "t.txt"
        transcript.write_text("raw talk", encoding="utf-8")
        result = run.cli(
            "convert",
            "--folder", "projects/eurohpc",
            "--transcript-file", str(transcript),
            input="n\n",
        )
        assert result.exit_code != 0
        assert "Abort" in result.output

    def test_editor_transcript(
        self,
        run: type[_Runner],
        fake_llm: FakeLLMClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_llm.responses = ["# Minutes"]
        monkeypatch.setattr(cli_main.click, "edit", lambda _="": "edited transcript")
        result = run.cli(
            "convert", "--folder", "projects/eurohpc", "--yes", input="editor\n"
        )
        assert result.exit_code == 0
        assert "saved:" in result.output

    def test_empty_editor_fails(
        self,
        run: type[_Runner],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(cli_main.click, "edit", lambda _="": None)
        result = run.cli(
            "convert", "--folder", "projects/eurohpc", "--yes", input="editor\n"
        )
        assert result.exit_code != 0
        assert "empty transcript" in result.output

    def test_api_error_reported(
        self, run: type[_Runner], tmp_path: Path
    ) -> None:
        transcript = tmp_path / "t.txt"
        transcript.write_text("x", encoding="utf-8")
        result = run.cli(
            "convert",
            "--folder", "../evil",
            "--transcript-file", str(transcript),
            "--yes",
        )
        assert result.exit_code != 0
        assert "escapes notes root" in result.output


class TestCleanup:
    def test_direct(self, run: type[_Runner], fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = ["# Clean"]
        result = run.cli("cleanup", "todo.md", "--yes")
        assert result.exit_code == 0
        assert "# Clean" in result.output

    def test_interactive_pick_and_confirm(
        self, run: type[_Runner], fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = ["# Clean"]
        result = run.cli("cleanup", input="4\ny\n")
        assert result.exit_code == 0
        assert "Notes:" in result.output
        assert "# Clean" in result.output


class TestContext:
    def test_context(self, run: type[_Runner]) -> None:
        result = run.cli("context", "projects/eurohpc")
        assert result.exit_code == 0
        assert "ROOT CONTEXT" in result.output
        assert "PROJECTS CONTEXT" in result.output

    def test_no_context(self, run: type[_Runner], notes_root: Path) -> None:
        (notes_root / "AGENTS.md").unlink()
        (notes_root / "projects" / "AGENTS.md").unlink()
        result = run.cli("context")
        assert "(no context files on this path)" in result.output


class TestConfigOption:
    def test_bad_config_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{}", encoding="utf-8")
        result = CliRunner().invoke(cli_main.cli, ["--config", str(bad), "ls"])
        assert result.exit_code != 0
        assert "invalid" in result.output


class TestGuidedEdgeCases:
    def test_pick_note_empty_tree(
        self, run: type[_Runner], notes_root: Path
    ) -> None:
        import shutil

        shutil.rmtree(notes_root / "projects")
        (notes_root / "AGENTS.md").unlink()
        (notes_root / "todo.md").unlink()
        result = run.cli("show")
        assert result.exit_code != 0
        assert "no notes found" in result.output

    def test_transcript_file_prompted_path(
        self, run: type[_Runner], fake_llm: FakeLLMClient, tmp_path: Path
    ) -> None:
        fake_llm.responses = ["# Minutes"]
        transcript = tmp_path / "t.txt"
        transcript.write_text("raw", encoding="utf-8")
        result = run.cli(
            "convert", "--folder", "projects/eurohpc", "--yes",
            input=f"file\n{transcript}\n",
        )
        assert result.exit_code == 0
        assert "saved:" in result.output

    def test_cleanup_declined(self, run: type[_Runner]) -> None:
        result = run.cli("cleanup", "todo.md", input="n\n")
        assert result.exit_code != 0

    def test_cleanup_api_error(self, run: type[_Runner]) -> None:
        result = run.cli("cleanup", "nope.md", "--yes")
        assert result.exit_code != 0
        assert "no such note" in result.output

    def test_context_api_error(self, run: type[_Runner]) -> None:
        result = run.cli("context", "../evil")
        assert result.exit_code != 0
        assert "escapes notes root" in result.output

    def test_query_semantic_reason_shown(
        self, run: type[_Runner], fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = [
            json.dumps({"keywords": ["eurohpc"], "semantic_question": "which?"}),
            json.dumps(
                [
                    {
                        "path": "projects/eurohpc/2026-08-01-kickoff.md",
                        "reason": "the reason",
                    }
                ]
            ),
        ]
        result = run.cli("query", "which meeting?")
        assert "— the reason" in result.output
