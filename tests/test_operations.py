"""Tests for mimama.core.operations."""

from datetime import date
from pathlib import Path

import pytest

from mimama.core.notes import NoteStore
from mimama.core.operations import (
    cleanup_note,
    default_minutes_filename,
    generate_note_content,
    transcript_to_minutes,
)
from tests.fakes import FakeLLMClient


@pytest.fixture()
def store(tmp_path: Path) -> NoteStore:
    (tmp_path / "AGENTS.md").write_text("ROOT CONTEXT", encoding="utf-8")
    meetings = tmp_path / "meetings"
    meetings.mkdir()
    (meetings / "AGENTS.md").write_text("MEETINGS CONTEXT", encoding="utf-8")
    (meetings / "rough.md").write_text("rough notes here", encoding="utf-8")
    return NoteStore(root=tmp_path)


class TestTranscriptToMinutes:
    def test_writes_generated_minutes(self, store: NoteStore) -> None:
        llm = FakeLLMClient(responses=["# Minutes\ndone"])
        result = transcript_to_minutes(
            store, llm, "meetings", "raw transcript", filename="kickoff.md"
        )
        assert result.path == "meetings/kickoff.md"
        assert result.content == "# Minutes\ndone"
        assert store.read("meetings/kickoff.md") == "# Minutes\ndone"

    def test_hierarchical_context_in_system_prompt(self, store: NoteStore) -> None:
        llm = FakeLLMClient()
        transcript_to_minutes(store, llm, "meetings", "t", filename="x.md")
        system, user = llm.calls[0]
        assert "ROOT CONTEXT" in system
        assert "MEETINGS CONTEXT" in system
        assert system.index("ROOT CONTEXT") < system.index("MEETINGS CONTEXT")
        assert "raw transcript" not in system
        assert "t" in user

    def test_transcript_in_user_prompt(self, store: NoteStore) -> None:
        llm = FakeLLMClient()
        transcript_to_minutes(
            store, llm, "meetings", "THE TRANSCRIPT BODY", filename="x.md"
        )
        _, user = llm.calls[0]
        assert "THE TRANSCRIPT BODY" in user

    def test_default_filename_is_dated(self, store: NoteStore) -> None:
        llm = FakeLLMClient()
        result = transcript_to_minutes(store, llm, "meetings", "t")
        assert result.path == f"meetings/{date.today().isoformat()}-minutes.md"

    def test_no_context_folder(self, store: NoteStore) -> None:
        (store.root / "AGENTS.md").unlink()
        (store.root / "meetings" / "AGENTS.md").unlink()
        llm = FakeLLMClient()
        transcript_to_minutes(store, llm, "meetings", "t", filename="x.md")
        system, _ = llm.calls[0]
        assert "Context for this folder" not in system

    def test_context_and_instructions_in_user_prompt(self, store: NoteStore) -> None:
        llm = FakeLLMClient()
        transcript_to_minutes(
            store,
            llm,
            "meetings",
            "THE TRANSCRIPT BODY",
            filename="x.md",
            instructions="make bullets",
            context="EXTRA CONTEXT",
        )
        system, user = llm.calls[0]
        assert "EXTRA CONTEXT" not in system
        assert "EXTRA CONTEXT" in user
        assert "make bullets" in user
        assert "THE TRANSCRIPT BODY" in user
        assert user.index("EXTRA CONTEXT") < user.index("make bullets")
        assert user.index("make bullets") < user.index("THE TRANSCRIPT BODY")

    def test_no_context_leaves_prompt_unchanged(self, store: NoteStore) -> None:
        llm = FakeLLMClient()
        transcript_to_minutes(store, llm, "meetings", "t", filename="x.md")
        _, user = llm.calls[0]
        assert user.startswith(
            "Read the following transcript and produce meeting minutes"
        )


class TestGenerateNoteContent:
    def test_combines_context_and_content(self, store: NoteStore) -> None:
        llm = FakeLLMClient(responses=["generated"])
        result = generate_note_content(
            store, llm, "meetings/new.md", "write about X", "some seed content"
        )
        assert result == "generated"
        _, user = llm.calls[0]
        assert "write about X" in user
        assert "some seed content" in user

    def test_no_context_falls_back_to_content(self, store: NoteStore) -> None:
        llm = FakeLLMClient()
        generate_note_content(store, llm, "meetings/new.md", "", "just content")
        _, user = llm.calls[0]
        assert user == "just content"

    def test_hierarchical_system_context(self, store: NoteStore) -> None:
        llm = FakeLLMClient()
        generate_note_content(store, llm, "meetings/new.md", "c", "x")
        system, _ = llm.calls[0]
        assert "ROOT CONTEXT" in system
        assert "MEETINGS CONTEXT" in system


class TestCleanupNote:
    def test_rewrites_note_in_place(self, store: NoteStore) -> None:
        llm = FakeLLMClient(responses=["# Clean\nnice"])
        result = cleanup_note(store, llm, "meetings/rough.md")
        assert result.path == "meetings/rough.md"
        assert store.read("meetings/rough.md") == "# Clean\nnice"

    def test_original_content_in_user_prompt(self, store: NoteStore) -> None:
        llm = FakeLLMClient()
        cleanup_note(store, llm, "meetings/rough.md")
        _, user = llm.calls[0]
        assert "rough notes here" in user

    def test_context_included(self, store: NoteStore) -> None:
        llm = FakeLLMClient()
        cleanup_note(store, llm, "meetings/rough.md")
        system, _ = llm.calls[0]
        assert "MEETINGS CONTEXT" in system


def test_default_minutes_filename() -> None:
    assert default_minutes_filename(date(2026, 8, 30)) == "2026-08-30-minutes.md"
    assert default_minutes_filename().endswith("-minutes.md")
