"""Tests for the /gui htmx surface (HTML fragments)."""

import json

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeLLMClient


def _run_job(client: TestClient, start_url: str, stream_url_base: str, **kwargs: object) -> dict:
    """POST to a job-start endpoint, drain its SSE stream, and return the
    parsed ``done`` event payload."""
    start = client.post(start_url, **kwargs)
    assert start.status_code == 200
    job_id = start.json()["job_id"]
    with client.stream("GET", f"{stream_url_base}/{job_id}") as response:
        body = "".join(response.iter_text())
    assert "event: done" in body
    _, _, tail = body.rpartition("event: done\ndata: ")
    return json.loads(tail.split("\n\n", 1)[0])


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
    def test_new_note_form_lists_context(self, client: TestClient) -> None:
        response = client.get(
            "/gui/note/create", params={"path": "projects/eurohpc"}
        )
        assert response.status_code == 200
        assert "ROOT CONTEXT" in response.text
        assert "PROJECTS CONTEXT" in response.text

    def test_new_note_form_uses_multipart_encoding(self, client: TestClient) -> None:
        response = client.get("/gui/note/create")
        assert response.status_code == 200
        # htmx 2.x no longer auto-detects file inputs, so the form must declare
        # multipart encoding explicitly or uploaded context files are sent as a
        # urlencoded string and FastAPI rejects the request with a 422.
        assert 'enctype="multipart/form-data"' in response.text

    def test_plain_create(self, client: TestClient) -> None:
        response = client.post(
            "/gui/note/create",
            data={"folder": "projects/eurohpc", "filename": "plain.md", "content": "hi"},
        )
        assert "Saved: projects/eurohpc/plain.md" in response.text
        assert "hi" in response.text

    def test_ai_transcript_writes_and_renders(
        self, client: TestClient, fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = ["# Minutes body"]
        response = client.post(
            "/gui/note/create",
            data={
                "folder": "projects/eurohpc",
                "filename": "o.md",
                "content": "t",
                "ai": "on",
                "transcript": "on",
                "transcript_instructions": "custom instructions",
                "context": "EXTRA CONTEXT",
            },
        )
        assert "Saved: projects/eurohpc/o.md" in response.text
        assert "# Minutes body" in response.text
        _, user = fake_llm.calls[0]
        assert "custom instructions" in user
        assert "EXTRA CONTEXT" in user
        assert "t" in user

    def test_default_filename_when_blank(self, client: TestClient) -> None:
        response = client.post(
            "/gui/note/create",
            data={"folder": "projects/eurohpc", "filename": "", "content": "x"},
        )
        assert "Saved: projects/eurohpc/" in response.text

    def test_submit_error_rendered(self, client: TestClient) -> None:
        response = client.post(
            "/gui/note/create",
            data={"folder": "../evil", "filename": "x.md", "content": "t"},
        )
        assert "escapes notes root" in response.text

    def test_ai_transcript_includes_uploaded_context_files(
        self, client: TestClient, fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = ["# Minutes body"]
        response = client.post(
            "/gui/note/create",
            data={
                "folder": "projects/eurohpc",
                "filename": "o.md",
                "content": "t",
                "ai": "on",
                "transcript": "on",
            },
            files={"context_files": ("ref.txt", b"UPLOADED FILE DATA", "text/plain")},
        )
        assert response.status_code == 200
        _, user = fake_llm.calls[0]
        assert "# Attached file: ref.txt" in user
        assert "UPLOADED FILE DATA" in user


class TestGenerate:
    """Free-form AI generation: a streamed job that writes the note itself."""

    def test_ai_generation_writes_and_renders(
        self, client: TestClient, fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = ["generated body"]
        payload = _run_job(
            client,
            "/gui/note/create/generate/start",
            "/gui/note/create/generate/stream",
            data={
                "folder": "projects/eurohpc",
                "filename": "gen.md",
                "content": "seed",
                "context": "write about X",
            },
        )
        assert payload["error"] is None
        assert payload["cancelled"] is False
        assert "Saved: projects/eurohpc/gen.md" in payload["html"]
        assert "generated body" in payload["html"]
        _, user = fake_llm.calls[0]
        assert "write about X" in user
        assert "seed" in user
        # the note was actually written to disk
        note = client.get("/gui/note", params={"path": "projects/eurohpc/gen.md"})
        assert "generated body" in note.text

    def test_error_surfaced(self, client: TestClient) -> None:
        payload = _run_job(
            client,
            "/gui/note/create/generate/start",
            "/gui/note/create/generate/stream",
            data={"folder": "../evil", "filename": "x.md", "content": "t"},
        )
        assert payload["error"] is not None
        assert "escapes notes root" in payload["error"]

    def test_uploaded_context_files_reach_prompt(
        self, client: TestClient, fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = ["generated body"]
        payload = _run_job(
            client,
            "/gui/note/create/generate/start",
            "/gui/note/create/generate/stream",
            data={
                "folder": "projects/eurohpc",
                "filename": "gen.md",
                "content": "seed",
                "context": "write about X",
            },
            files={"context_files": ("ref.txt", b"UPLOADED FILE DATA", "text/plain")},
        )
        assert payload["error"] is None
        _, user = fake_llm.calls[0]
        assert "# Attached file: ref.txt" in user
        assert "UPLOADED FILE DATA" in user

    def test_cancel_sets_job_cancel_event(
        self, client: TestClient, app, fake_llm: FakeLLMClient  # type: ignore[no-untyped-def]
    ) -> None:
        import threading

        fake_llm.responses = ["one two three four five"]
        fake_llm.pause_after_first_chunk = threading.Event()
        start = client.post(
            "/gui/note/create/generate/start",
            data={
                "folder": "projects/eurohpc",
                "filename": "cancelled.md",
                "content": "seed",
            },
        )
        job_id = start.json()["job_id"]
        registry = app.state.inference_jobs
        job = registry.get(job_id)
        assert job is not None
        assert not job.cancel_event.is_set()

        cancel = client.post(f"/gui/note/create/generate/cancel/{job_id}")
        assert cancel.json() == {"ok": True}
        assert job.cancel_event.is_set()

        # let the (now-cancelled) background job actually finish so its
        # daemon thread doesn't outlive the test. Depending on scheduling,
        # cancellation may land before or after the first chunk is emitted.
        fake_llm.pause_after_first_chunk.set()
        from mimama.core.inference_jobs import DONE as JOB_DONE

        chunks = []
        while (item := job.lines.get(timeout=5)) is not JOB_DONE:
            chunks.append(item)
        assert chunks in ([], ["one"])
        assert job.cancelled is True
        assert job.error is None
        # the note was never written
        note = client.get("/gui/note", params={"path": "projects/eurohpc/cancelled.md"})
        assert "no such note" in note.text

    def test_cancel_unknown_job_is_a_no_op(self, client: TestClient) -> None:
        response = client.post("/gui/note/create/generate/cancel/nope")
        assert response.json() == {"ok": True}

    def test_unknown_job(self, client: TestClient) -> None:
        with client.stream(
            "GET", "/gui/note/create/generate/stream/nope"
        ) as response:
            body = "".join(response.iter_text())
        assert "unknown generation job" in body


class TestTranscribeUpload:
    def test_start_and_stream(self, client: TestClient) -> None:
        start = client.post(
            "/gui/transcribe/start",
            files={"audio_file": ("meeting.wav", b"fake-wav-bytes", "audio/wav")},
        )
        assert start.status_code == 200
        job_id = start.json()["job_id"]

        with client.stream("GET", f"/gui/transcribe/stream/{job_id}") as response:
            body = "".join(response.iter_text())
        assert "event: done" in body

    def test_stream_unknown_job(self, client: TestClient) -> None:
        with client.stream("GET", "/gui/transcribe/stream/nope") as response:
            body = "".join(response.iter_text())
        assert "unknown transcription job" in body



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
        payload = _run_job(
            client,
            "/gui/query/start",
            "/gui/query/stream",
            data={"question": "eurohpc notes"},
        )
        assert payload["error"] is None
        assert "2026-08-29-sync.md" in payload["html"]

    def test_no_results(self, client: TestClient, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = [json.dumps({"keywords": ["zzzz"]})]
        payload = _run_job(
            client, "/gui/query/start", "/gui/query/stream", data={"question": "zzzz"}
        )
        assert "No matching notes" in payload["html"]

    def test_error_rendered(self, client: TestClient, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = ["bad", "bad"]
        payload = _run_job(
            client, "/gui/query/start", "/gui/query/stream", data={"question": "q"}
        )
        assert payload["error"] is not None
        assert "valid query plan" in payload["error"]

    def test_unknown_job(self, client: TestClient) -> None:
        with client.stream("GET", "/gui/query/stream/nope") as response:
            body = "".join(response.iter_text())
        assert "unknown query job" in body

    def test_cancel_unknown_job_is_a_no_op(self, client: TestClient) -> None:
        response = client.post("/gui/query/cancel/nope")
        assert response.json() == {"ok": True}


class TestRename:
    def test_form_prefills_old_path(self, client: TestClient) -> None:
        response = client.get("/gui/note/rename", params={"old_path": "todo.md"})
        assert response.status_code == 200
        assert "todo.md" in response.text

    def test_submit_renames(self, client: TestClient) -> None:
        response = client.post(
            "/gui/note/rename", data={"old_path": "todo.md", "new_path": "renamed.md"}
        )
        assert "buy milk" in response.text
        assert client.get("/gui/note", params={"path": "todo.md"}).text.count(
            "no such note"
        ) == 1

    def test_missing_note_error(self, client: TestClient) -> None:
        response = client.post(
            "/gui/note/rename", data={"old_path": "nope.md", "new_path": "x.md"}
        )
        assert "no such note" in response.text


class TestDelete:
    def test_submit_deletes(self, client: TestClient) -> None:
        response = client.post("/gui/note/delete", data={"path": "todo.md"})
        assert response.status_code == 200
        assert client.get("/api/note", params={"path": "todo.md"}).status_code == 404

    def test_missing_note_error(self, client: TestClient) -> None:
        response = client.post("/gui/note/delete", data={"path": "nope.md"})
        assert "no such note" in response.text


class TestInference:
    def test_form_shows_context(self, client: TestClient) -> None:
        response = client.get(
            "/gui/note/inference", params={"path": "projects/eurohpc"}
        )
        assert "ROOT CONTEXT" in response.text

    def test_submit_runs_prompt(self, client: TestClient, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = ["the answer"]
        payload = _run_job(
            client,
            "/gui/note/inference/start",
            "/gui/note/inference/stream",
            data={"path": "projects/eurohpc", "prompt": "summarize"},
        )
        assert payload["error"] is None
        assert "the answer" in payload["html"]

    def test_missing_note_error(self, client: TestClient) -> None:
        payload = _run_job(
            client,
            "/gui/note/inference/start",
            "/gui/note/inference/stream",
            data={"path": "../evil", "prompt": "x"},
        )
        assert payload["error"] is not None
        assert "escapes notes root" in payload["error"]

    def test_unknown_job(self, client: TestClient) -> None:
        with client.stream("GET", "/gui/note/inference/stream/nope") as response:
            body = "".join(response.iter_text())
        assert "unknown inference job" in body

    def test_cancel_unknown_job_is_a_no_op(self, client: TestClient) -> None:
        response = client.post("/gui/note/inference/cancel/nope")
        assert response.json() == {"ok": True}
    """notes.git=true stages every mutation made through /gui, not just /api."""

    @pytest.fixture()
    def git_client(self, config, notes_root, fake_llm: FakeLLMClient):  # type: ignore[no-untyped-def]
        import subprocess

        from mimama.server.app import create_app

        subprocess.run(["git", "init", "-q"], cwd=notes_root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@example.com"], cwd=notes_root, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=notes_root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=notes_root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=notes_root, check=True)
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

    def test_create_stages(self, git_client: TestClient, notes_root) -> None:  # type: ignore[no-untyped-def]
        git_client.post(
            "/gui/note/create",
            data={"folder": "", "filename": "staged.md", "content": "x"},
        )
        assert "staged.md" in self._staged(notes_root)

    def test_rename_stages_both_paths(self, git_client: TestClient, notes_root) -> None:  # type: ignore[no-untyped-def]
        git_client.post(
            "/gui/note/rename", data={"old_path": "todo.md", "new_path": "renamed.md"}
        )
        staged = self._staged(notes_root)
        assert "todo.md" in staged
        assert "renamed.md" in staged

    def test_delete_stages(self, git_client: TestClient, notes_root) -> None:  # type: ignore[no-untyped-def]
        git_client.post("/gui/note/delete", data={"path": "todo.md"})
        assert "todo.md" in self._staged(notes_root)

    def test_cleanup_stages(  # type: ignore[no-untyped-def]
        self, git_client: TestClient, notes_root, fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = ["# Clean"]
        git_client.post("/gui/cleanup", data={"path": "todo.md"})
        assert "todo.md" in self._staged(notes_root)
