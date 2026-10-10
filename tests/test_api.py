"""Tests for the /api JSON surface."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mimama.server import schemas
from tests.fakes import FakeLLMClient


class TestTree:
    def test_root(self, client: TestClient) -> None:
        response = client.get("/api/tree")
        assert response.status_code == 200
        tree = schemas.TreeResponse.model_validate(response.json())
        names = [e.name for e in tree.entries]
        assert names == ["projects", "AGENTS.md", "todo.md"]

    def test_subdir(self, client: TestClient) -> None:
        tree = schemas.TreeResponse.model_validate(
            client.get("/api/tree", params={"path": "projects/eurohpc"}).json()
        )
        assert len(tree.entries) == 2

    def test_missing_dir_404(self, client: TestClient) -> None:
        assert client.get("/api/tree", params={"path": "nope"}).status_code == 404

    def test_escape_404(self, client: TestClient) -> None:
        assert client.get("/api/tree", params={"path": "../.."}).status_code == 404


class TestFolders:
    def test_folders(self, client: TestClient) -> None:
        folders = schemas.FoldersResponse.model_validate(
            client.get("/api/folders").json()
        )
        assert folders.folders == ["", "projects", "projects/eurohpc"]


class TestNote:
    def test_read(self, client: TestClient) -> None:
        note = schemas.NoteContent.model_validate(
            client.get("/api/note", params={"path": "todo.md"}).json()
        )
        assert note.content == "buy milk"

    def test_read_missing_404(self, client: TestClient) -> None:
        response = client.get("/api/note", params={"path": "nope.md"})
        assert response.status_code == 404
        assert "no such note" in response.json()["detail"]

    def test_write(self, client: TestClient) -> None:
        body = schemas.NoteWriteRequest(path="new/note.md", content="hello")
        response = client.put("/api/note", json=body.model_dump())
        assert response.status_code == 200
        written = schemas.NoteWriteResponse.model_validate(response.json())
        assert written.path == "new/note.md"
        assert (
            client.get("/api/note", params={"path": "new/note.md"}).json()["content"]
            == "hello"
        )

    def test_write_bad_path_400(self, client: TestClient) -> None:
        body = schemas.NoteWriteRequest(path="../evil.md", content="x")
        assert client.put("/api/note", json=body.model_dump()).status_code == 400


class TestDelete:
    def test_delete(self, client: TestClient) -> None:
        body = schemas.NoteDeleteRequest(path="todo.md")
        response = client.request("DELETE", "/api/note", json=body.model_dump())
        assert response.status_code == 200
        assert client.get("/api/note", params={"path": "todo.md"}).status_code == 404

    def test_missing_note_404(self, client: TestClient) -> None:
        body = schemas.NoteDeleteRequest(path="nope.md")
        response = client.request("DELETE", "/api/note", json=body.model_dump())
        assert response.status_code == 404


class TestRename:
    def test_rename(self, client: TestClient) -> None:
        body = schemas.NoteRenameRequest(old_path="todo.md", new_path="renamed.md")
        response = client.post("/api/note/rename", json=body.model_dump())
        assert response.status_code == 200
        assert client.get("/api/note", params={"path": "todo.md"}).status_code == 404
        assert (
            client.get("/api/note", params={"path": "renamed.md"}).json()["content"]
            == "buy milk"
        )

    def test_missing_note_400(self, client: TestClient) -> None:
        body = schemas.NoteRenameRequest(old_path="nope.md", new_path="x.md")
        response = client.post("/api/note/rename", json=body.model_dump())
        assert response.status_code == 400


class TestCreate:
    def test_create(self, client: TestClient) -> None:
        body = schemas.NoteCreateRequest(path="brand-new.md", content="hi")
        response = client.post("/api/note/create", json=body.model_dump())
        assert response.status_code == 200
        result = schemas.NoteCreateResponse.model_validate(response.json())
        assert result.path == "brand-new.md"
        assert (
            client.get("/api/note", params={"path": "brand-new.md"}).json()["content"]
            == "hi"
        )

    def test_bad_path_400(self, client: TestClient) -> None:
        body = schemas.NoteCreateRequest(path="../evil.md")
        response = client.post("/api/note/create", json=body.model_dump())
        assert response.status_code == 400


class TestGitStaging:
    """Git integration end-to-end: notes.git=true stages every mutation."""

    @pytest.fixture()
    def git_client(self, config, notes_root, fake_llm: FakeLLMClient):  # type: ignore[no-untyped-def]
        import subprocess

        from mimama.server.app import create_app

        subprocess.run(["git", "init", "-q"], cwd=notes_root, check=True)
        config.notes.git = True
        app = create_app(config=config, llm=fake_llm)
        with TestClient(app) as test_client:
            yield test_client

    def _staged(self, notes_root) -> set[str]:  # type: ignore[no-untyped-def]
        import subprocess

        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--no-renames"],
            cwd=notes_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return set(result.stdout.split())

    def test_write_stages(self, git_client: TestClient, notes_root) -> None:  # type: ignore[no-untyped-def]
        body = schemas.NoteWriteRequest(path="staged.md", content="x")
        git_client.put("/api/note", json=body.model_dump())
        assert "staged.md" in self._staged(notes_root)

    def test_create_stages(self, git_client: TestClient, notes_root) -> None:  # type: ignore[no-untyped-def]
        body = schemas.NoteCreateRequest(path="created.md", content="x")
        git_client.post("/api/note/create", json=body.model_dump())
        assert "created.md" in self._staged(notes_root)

    def test_convert_stages(self, git_client: TestClient, notes_root, fake_llm) -> None:  # type: ignore[no-untyped-def]
        fake_llm.responses = ["# Minutes"]
        body = schemas.ConvertRequest(folder="", transcript="t", filename="conv.md")
        git_client.post("/api/operations/convert", json=body.model_dump())
        assert "conv.md" in self._staged(notes_root)


class TestContext:
    def test_hierarchical(self, client: TestClient) -> None:
        ctx = schemas.ContextResponse.model_validate(
            client.get("/api/context", params={"path": "projects/eurohpc"}).json()
        )
        assert "ROOT CONTEXT" in ctx.context
        assert "PROJECTS CONTEXT" in ctx.context

    def test_missing_path_404(self, client: TestClient) -> None:
        assert client.get("/api/context", params={"path": "../x"}).status_code == 404


class TestSearch:
    def test_hits(self, client: TestClient) -> None:
        body = schemas.SearchRequest(terms=["eurohpc"])
        response = client.post("/api/search", json=body.model_dump())
        hits = schemas.SearchResponse.model_validate(response.json()).hits
        assert {h.path for h in hits} == {
            "projects/eurohpc/2026-08-29-sync.md",
            "projects/eurohpc/2026-08-01-kickoff.md",
        }

    def test_subtree_scope(self, client: TestClient) -> None:
        body = schemas.SearchRequest(terms=["milk"], subtree="projects")
        response = client.post("/api/search", json=body.model_dump())
        assert schemas.SearchResponse.model_validate(response.json()).hits == []

    def test_bad_subtree_404(self, client: TestClient) -> None:
        body = schemas.SearchRequest(terms=["x"], subtree="nope")
        assert client.post("/api/search", json=body.model_dump()).status_code == 404


class TestConvert:
    def test_creates_minutes(self, client: TestClient, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = ["# Minutes"]
        body = schemas.ConvertRequest(
            folder="projects/eurohpc", transcript="raw talk", filename="out.md"
        )
        response = client.post("/api/operations/convert", json=body.model_dump())
        assert response.status_code == 200
        result = schemas.OperationResponse.model_validate(response.json())
        assert result.path == "projects/eurohpc/out.md"
        assert result.content == "# Minutes"
        # context was assembled hierarchically
        system, user = fake_llm.calls[0]
        assert "ROOT CONTEXT" in system and "PROJECTS CONTEXT" in system
        assert "raw talk" in user

    def test_bad_folder_400(self, client: TestClient) -> None:
        body = schemas.ConvertRequest(folder="../evil", transcript="t")
        response = client.post("/api/operations/convert", json=body.model_dump())
        assert response.status_code == 400

    def test_instructions_and_context_forwarded(
        self, client: TestClient, fake_llm: FakeLLMClient
    ) -> None:
        body = schemas.ConvertRequest(
            folder="projects/eurohpc",
            transcript="raw talk",
            filename="out.md",
            instructions="make bullets",
            context="EXTRA CONTEXT",
        )
        response = client.post("/api/operations/convert", json=body.model_dump())
        assert response.status_code == 200
        _, user = fake_llm.calls[0]
        assert "EXTRA CONTEXT" in user
        assert "make bullets" in user
        assert "raw talk" in user
        assert user.index("EXTRA CONTEXT") < user.index("make bullets")

    def test_context_files_read_and_forwarded(
        self, client: TestClient, fake_llm: FakeLLMClient, tmp_path: Path
    ) -> None:
        fake_llm.responses = ["# Minutes"]
        ref = tmp_path / "ref.txt"
        ref.write_text("SERVER FILE DATA", encoding="utf-8")
        body = schemas.ConvertRequest(
            folder="projects/eurohpc",
            transcript="raw talk",
            filename="out.md",
            context_files=[str(ref)],
        )
        response = client.post("/api/operations/convert", json=body.model_dump())
        assert response.status_code == 200
        _, user = fake_llm.calls[0]
        assert "# Attached file: ref.txt" in user
        assert "SERVER FILE DATA" in user

    def test_missing_context_file_400(
        self, client: TestClient, fake_llm: FakeLLMClient
    ) -> None:
        body = schemas.ConvertRequest(
            folder="projects/eurohpc",
            transcript="t",
            context_files=["/does/not/exist.txt"],
        )
        response = client.post("/api/operations/convert", json=body.model_dump())
        assert response.status_code == 400
        assert "no such file" in response.json()["detail"]


class TestCleanup:
    def test_rewrites(self, client: TestClient, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = ["# Clean"]
        body = schemas.CleanupRequest(path="todo.md")
        response = client.post("/api/operations/cleanup", json=body.model_dump())
        result = schemas.OperationResponse.model_validate(response.json())
        assert result.content == "# Clean"

    def test_missing_note_404(self, client: TestClient) -> None:
        body = schemas.CleanupRequest(path="nope.md")
        response = client.post("/api/operations/cleanup", json=body.model_dump())
        assert response.status_code == 404


class TestInference:
    def test_runs_with_context(self, client: TestClient, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = ["result"]
        body = schemas.InferenceRequest(path="projects/eurohpc", prompt="summarize")
        response = client.post("/api/operations/inference", json=body.model_dump())
        assert response.status_code == 200
        result = schemas.InferenceResponse.model_validate(response.json())
        assert result.content == "result"
        system, user = fake_llm.calls[0]
        assert "ROOT CONTEXT" in system
        assert user == "summarize"


class TestQuery:
    def test_filter_query(self, client: TestClient, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = [
            json.dumps(
                {
                    "keywords": ["eurohpc"],
                    "date_from": "2026-08-28",
                    "date_to": "2026-08-30",
                    "subtree": None,
                    "semantic_question": None,
                }
            )
        ]
        body = schemas.QueryRequest(question="eurohpc notes in the last 2 days")
        response = client.post("/api/query", json=body.model_dump())
        assert response.status_code == 200
        result = schemas.QueryResponse.model_validate(response.json())
        assert [r.path for r in result.results] == [
            "projects/eurohpc/2026-08-29-sync.md"
        ]
        assert result.plan.keywords == ["eurohpc"]

    def test_semantic_query(self, client: TestClient, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = [
            json.dumps({"keywords": ["eurohpc"], "semantic_question": "decided?"}),
            json.dumps(
                [
                    {
                        "path": "projects/eurohpc/2026-08-01-kickoff.md",
                        "reason": "litellm decision",
                    }
                ]
            ),
        ]
        body = schemas.QueryRequest(question="where did we decide on litellm?")
        result = schemas.QueryResponse.model_validate(
            client.post("/api/query", json=body.model_dump()).json()
        )
        assert result.results[0].reason == "litellm decision"

    def test_unplannable_query_502(
        self, client: TestClient, fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = ["garbage", "more garbage"]
        body = schemas.QueryRequest(question="q")
        response = client.post("/api/query", json=body.model_dump())
        assert response.status_code == 502


class TestLLMFailures:
    """A broken LLM endpoint surfaces as 502 on every LLM-backed route."""

    @pytest.fixture()
    def error_client(self, config):  # type: ignore[no-untyped-def]
        from mimama.server.app import create_app
        from tests.fakes import ErrorLLMClient

        app = create_app(config=config, llm=ErrorLLMClient())
        with TestClient(app) as test_client:
            yield test_client

    def test_convert_502(self, error_client: TestClient) -> None:
        body = schemas.ConvertRequest(folder="projects/eurohpc", transcript="t")
        response = error_client.post(
            "/api/operations/convert", json=body.model_dump()
        )
        assert response.status_code == 502
        assert "endpoint down" in response.json()["detail"]

    def test_cleanup_502(self, error_client: TestClient) -> None:
        body = schemas.CleanupRequest(path="todo.md")
        response = error_client.post(
            "/api/operations/cleanup", json=body.model_dump()
        )
        assert response.status_code == 502

    def test_query_502(self, error_client: TestClient) -> None:
        body = schemas.QueryRequest(question="q")
        response = error_client.post("/api/query", json=body.model_dump())
        assert response.status_code == 502
