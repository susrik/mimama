"""Tests for mimama.core.context."""

from pathlib import Path

import pytest

from mimama.core.context import assemble_context, list_context_files
from mimama.core.notes import NoteStore


@pytest.fixture()
def store(tmp_path: Path) -> NoteStore:
    (tmp_path / "AGENTS.md").write_text("ROOT CONTEXT", encoding="utf-8")
    a = tmp_path / "a"
    ab = a / "b"
    ab.mkdir(parents=True)
    (a / "AGENTS.md").write_text("A CONTEXT", encoding="utf-8")
    (ab / "AGENTS.md").write_text("B CONTEXT", encoding="utf-8")
    (ab / "note.md").write_text("a note", encoding="utf-8")
    nocontext = tmp_path / "plain"
    nocontext.mkdir()
    (nocontext / "note.md").write_text("plain note", encoding="utf-8")
    return NoteStore(root=tmp_path)


def test_root_first_ordering(store: NoteStore) -> None:
    ctx = assemble_context(store, "a/b")
    assert ctx.index("ROOT CONTEXT") < ctx.index("A CONTEXT") < ctx.index("B CONTEXT")


def test_file_path_uses_its_folder(store: NoteStore) -> None:
    assert assemble_context(store, "a/b/note.md") == assemble_context(store, "a/b")


def test_origin_headers(store: NoteStore) -> None:
    ctx = assemble_context(store, "a/b")
    assert "# Context from: (notes root)" in ctx
    assert "# Context from: a" in ctx
    assert "# Context from: a/b" in ctx


def test_missing_intermediate_context_skipped(store: NoteStore) -> None:
    ctx = assemble_context(store, "plain")
    assert ctx == "# Context from: (notes root)\n\nROOT CONTEXT"


def test_no_context_anywhere(store: NoteStore) -> None:
    (store.root / "AGENTS.md").unlink()
    assert assemble_context(store, "plain") == ""


def test_empty_context_file_skipped(store: NoteStore) -> None:
    (store.root / "a" / "AGENTS.md").write_text("   \n", encoding="utf-8")
    ctx = assemble_context(store, "a/b")
    assert "A CONTEXT" not in ctx
    assert "ROOT CONTEXT" in ctx and "B CONTEXT" in ctx


def test_root_folder_itself(store: NoteStore) -> None:
    assert assemble_context(store, "") == "# Context from: (notes root)\n\nROOT CONTEXT"


class TestListContextFiles:
    def test_root_first_ordering(self, store: NoteStore) -> None:
        files = list_context_files(store, "a/b")
        assert [f.origin for f in files] == ["", "a", "a/b"]
        assert [f.content for f in files] == ["ROOT CONTEXT", "A CONTEXT", "B CONTEXT"]

    def test_file_path_uses_its_folder(self, store: NoteStore) -> None:
        assert list_context_files(store, "a/b/note.md") == list_context_files(store, "a/b")

    def test_missing_intermediate_skipped(self, store: NoteStore) -> None:
        files = list_context_files(store, "plain")
        assert [f.origin for f in files] == [""]

    def test_empty_file_skipped(self, store: NoteStore) -> None:
        (store.root / "a" / "AGENTS.md").write_text("   \n", encoding="utf-8")
        files = list_context_files(store, "a/b")
        assert [f.origin for f in files] == ["", "a/b"]

    def test_no_context_anywhere(self, store: NoteStore) -> None:
        (store.root / "AGENTS.md").unlink()
        assert list_context_files(store, "plain") == []
