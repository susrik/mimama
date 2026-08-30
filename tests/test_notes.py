"""Tests for mimama.core.notes."""

import os
from datetime import date
from pathlib import Path

import pytest

from mimama.core.notes import NoteError, NoteStore


@pytest.fixture()
def store(tmp_path: Path) -> NoteStore:
    """A small notes tree:

    root/
      AGENTS.md
      todo.md
      projects/
        AGENTS.md
        eurohpc/
          2026-08-28-kickoff.md
          notes.md
      personal/
        journal.md
    """
    (tmp_path / "AGENTS.md").write_text("root context", encoding="utf-8")
    (tmp_path / "todo.md").write_text("buy milk", encoding="utf-8")
    projects = tmp_path / "projects"
    eurohpc = projects / "eurohpc"
    eurohpc.mkdir(parents=True)
    (projects / "AGENTS.md").write_text("projects context", encoding="utf-8")
    (eurohpc / "2026-08-28-kickoff.md").write_text(
        "# Kickoff\nEuroHPC kickoff meeting with Erik.", encoding="utf-8"
    )
    (eurohpc / "notes.md").write_text("misc EuroHPC notes", encoding="utf-8")
    personal = tmp_path / "personal"
    personal.mkdir()
    (personal / "journal.md").write_text("dear diary", encoding="utf-8")
    return NoteStore(root=tmp_path)


class TestPathSafety:
    def test_resolve_inside_root(self, store: NoteStore) -> None:
        assert store.resolve("todo.md").name == "todo.md"

    def test_resolve_root_itself(self, store: NoteStore) -> None:
        assert store.resolve("") == store.root

    @pytest.mark.parametrize("bad", ["../evil", "a/../../evil", "/etc/passwd"])
    def test_escape_rejected(self, store: NoteStore, bad: str) -> None:
        with pytest.raises(NoteError, match="escapes notes root"):
            store.resolve(bad)


class TestListDir:
    def test_root_listing_dirs_first(self, store: NoteStore) -> None:
        entries = store.list_dir("")
        names = [e.name for e in entries]
        assert names == ["personal", "projects", "AGENTS.md", "todo.md"]
        assert entries[0].is_dir and entries[1].is_dir

    def test_has_context_flag(self, store: NoteStore) -> None:
        entries = {e.name: e for e in store.list_dir("")}
        assert entries["projects"].has_context is True
        assert entries["personal"].has_context is False

    def test_subdir_listing(self, store: NoteStore) -> None:
        entries = store.list_dir("projects/eurohpc")
        assert [e.path for e in entries] == [
            "projects/eurohpc/2026-08-28-kickoff.md",
            "projects/eurohpc/notes.md",
        ]

    def test_hidden_entries_skipped(self, store: NoteStore) -> None:
        (store.root / ".git").mkdir()
        (store.root / ".hidden.md").write_text("x", encoding="utf-8")
        names = [e.name for e in store.list_dir("")]
        assert ".git" not in names and ".hidden.md" not in names

    def test_not_a_directory(self, store: NoteStore) -> None:
        with pytest.raises(NoteError, match="not a directory"):
            store.list_dir("todo.md")


class TestListFolders:
    def test_all_folders(self, store: NoteStore) -> None:
        assert store.list_folders() == [
            "",
            "personal",
            "projects",
            "projects/eurohpc",
        ]

    def test_hidden_folders_skipped(self, store: NoteStore) -> None:
        (store.root / ".git" / "objects").mkdir(parents=True)
        assert ".git" not in " ".join(store.list_folders())


class TestIterNotes:
    def test_excludes_agents_md_and_hidden(self, store: NoteStore) -> None:
        (store.root / ".hidden.md").write_text("x", encoding="utf-8")
        rels = [p.relative_to(store.root).as_posix() for p in store.iter_notes()]
        assert "AGENTS.md" not in rels
        assert "projects/AGENTS.md" not in rels
        assert ".hidden.md" not in rels
        assert "todo.md" in rels

    def test_subtree_scoped(self, store: NoteStore) -> None:
        rels = [
            p.relative_to(store.root).as_posix()
            for p in store.iter_notes("projects")
        ]
        assert rels == [
            "projects/eurohpc/2026-08-28-kickoff.md",
            "projects/eurohpc/notes.md",
        ]

    def test_bad_subtree(self, store: NoteStore) -> None:
        with pytest.raises(NoteError, match="not a directory"):
            store.iter_notes("todo.md")


class TestReadWrite:
    def test_read(self, store: NoteStore) -> None:
        assert store.read("todo.md") == "buy milk"

    def test_read_missing(self, store: NoteStore) -> None:
        with pytest.raises(NoteError, match="no such note"):
            store.read("nope.md")

    def test_write_creates_parents(self, store: NoteStore) -> None:
        rel = store.write("new/deep/note.md", "hello")
        assert rel == "new/deep/note.md"
        assert store.read(rel) == "hello"

    def test_write_overwrites(self, store: NoteStore) -> None:
        store.write("todo.md", "updated")
        assert store.read("todo.md") == "updated"

    @pytest.mark.parametrize("bad", ["", "folder/"])
    def test_write_invalid_path(self, store: NoteStore, bad: str) -> None:
        with pytest.raises(NoteError, match="not a valid note path"):
            store.write(bad, "x")


class TestSearch:
    def test_any_of_match(self, store: NoteStore) -> None:
        hits = store.search(["eurohpc"])
        assert {h.path for h in hits} == {
            "projects/eurohpc/2026-08-28-kickoff.md",
            "projects/eurohpc/notes.md",
        }

    def test_case_insensitive(self, store: NoteStore) -> None:
        assert store.search(["EUROHPC"])

    def test_line_info(self, store: NoteStore) -> None:
        hits = store.search(["kickoff meeting"])
        assert len(hits) == 1
        assert hits[0].line_number == 2
        assert "EuroHPC kickoff meeting" in hits[0].line

    def test_all_of_match(self, store: NoteStore) -> None:
        hits = store.search(["eurohpc", "erik"], any_of=False)
        assert [h.path for h in hits] == ["projects/eurohpc/2026-08-28-kickoff.md"]

    def test_scoped_to_subtree(self, store: NoteStore) -> None:
        assert store.search(["diary"], subtree="projects") == []
        assert store.search(["diary"], subtree="personal")

    def test_empty_terms(self, store: NoteStore) -> None:
        assert store.search([]) == []
        assert store.search([""]) == []

    def test_binaryish_file_skipped(self, store: NoteStore) -> None:
        (store.root / "blob.bin").write_bytes(b"\xff\xfe\x00searchterm")
        assert store.search(["searchterm"]) == []


class TestNoteDate:
    def test_frontmatter_date_wins(self, store: NoteStore) -> None:
        p = store.root / "2020-01-01-x.md"
        p.write_text("---\ndate: 2026-05-05\n---\nbody", encoding="utf-8")
        assert store.note_date(p) == date(2026, 5, 5)

    def test_filename_date_fallback(self, store: NoteStore) -> None:
        p = store.root / "projects/eurohpc/2026-08-28-kickoff.md"
        assert store.note_date(p) == date(2026, 8, 28)

    def test_mtime_fallback(self, store: NoteStore) -> None:
        p = store.root / "todo.md"
        mtime = date(2026, 3, 3)
        ts = float(
            __import__("datetime").datetime(2026, 3, 3, 12, 0).timestamp()
        )
        os.utime(p, (ts, ts))
        assert store.note_date(p) == mtime

    def test_invalid_frontmatter_date_falls_through(self, store: NoteStore) -> None:
        p = store.root / "2026-08-28-y.md"
        p.write_text("---\ndate: 2026-99-99\n---\nbody", encoding="utf-8")
        assert store.note_date(p) == date(2026, 8, 28)

    def test_invalid_filename_date_falls_to_mtime(self, store: NoteStore) -> None:
        p = store.root / "2026-99-99-z.md"
        p.write_text("body", encoding="utf-8")
        ts = float(__import__("datetime").datetime(2026, 4, 4, 12, 0).timestamp())
        os.utime(p, (ts, ts))
        assert store.note_date(p) == date(2026, 4, 4)

    def test_binaryish_head_ok(self, store: NoteStore) -> None:
        p = store.root / "2026-02-02-b.md"
        p.write_bytes(b"\xff\xfe\x00")
        assert store.note_date(p) == date(2026, 2, 2)
