"""Tests for the /gui htmx surface (HTML fragments)."""

import json

from fastapi.testclient import TestClient

from tests.fakes import FakeLLMClient


class TestIndex:
    def test_page_served(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert "mimama" in response.text
        assert "htmx" in response.text


class TestTree:
    def test_root_listing(self, client: TestClient) -> None:
        response = client.get("/gui/tree")
        assert response.status_code == 200
        assert "projects" in response.text
        assert "todo.md" in response.text

    def test_subdir_has_parent_link(self, client: TestClient) -> None:
        response = client.get("/gui/tree", params={"path": "projects/eurohpc"})
        assert "2026-08-29-sync.md" in response.text
        assert ".." in response.text

    def test_bad_path_shows_error(self, client: TestClient) -> None:
        response = client.get("/gui/tree", params={"path": "nope"})
        assert "not a directory" in response.text


class TestNote:
    def test_note_content_and_context(self, client: TestClient) -> None:
        response = client.get(
            "/gui/note", params={"path": "projects/eurohpc/2026-08-29-sync.md"}
        )
        assert "Erik presented the plan" in response.text
        assert "ROOT CONTEXT" in response.text

    def test_missing_note_error(self, client: TestClient) -> None:
        response = client.get("/gui/note", params={"path": "nope.md"})
        assert "no such note" in response.text


class TestConvert:
    def test_form_lists_folders(self, client: TestClient) -> None:
        response = client.get("/gui/convert")
        assert "projects/eurohpc" in response.text

    def test_submit_writes_and_renders(
        self, client: TestClient, fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = ["# Minutes body"]
        response = client.post(
            "/gui/convert",
            data={"folder": "projects/eurohpc", "transcript": "t", "filename": "o.md"},
        )
        assert "Saved: projects/eurohpc/o.md" in response.text
        assert "# Minutes body" in response.text

    def test_submit_default_filename(
        self, client: TestClient, fake_llm: FakeLLMClient
    ) -> None:
        response = client.post(
            "/gui/convert", data={"folder": "projects/eurohpc", "transcript": "t"}
        )
        assert "-minutes.md" in response.text

    def test_submit_error_rendered(self, client: TestClient) -> None:
        response = client.post(
            "/gui/convert", data={"folder": "../evil", "transcript": "t"}
        )
        assert "escapes notes root" in response.text


class TestCleanup:
    def test_submit(self, client: TestClient, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = ["# Cleaned"]
        response = client.post("/gui/cleanup", data={"path": "todo.md"})
        assert "# Cleaned" in response.text

    def test_missing_note_error(self, client: TestClient) -> None:
        response = client.post("/gui/cleanup", data={"path": "nope.md"})
        assert "no such note" in response.text


class TestQuery:
    def test_results_rendered(self, client: TestClient, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = [
            json.dumps({"keywords": ["eurohpc"], "semantic_question": None})
        ]
        response = client.post("/gui/query", data={"question": "eurohpc notes"})
        assert "2026-08-29-sync.md" in response.text

    def test_no_results(self, client: TestClient, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = [json.dumps({"keywords": ["zzzz"]})]
        response = client.post("/gui/query", data={"question": "zzzz"})
        assert "No matching notes" in response.text

    def test_error_rendered(self, client: TestClient, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = ["bad", "bad"]
        response = client.post("/gui/query", data={"question": "q"})
        assert "valid query plan" in response.text
