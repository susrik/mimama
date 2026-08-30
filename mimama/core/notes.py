"""Filesystem note tree: listing, read/write, full-text search.

All paths handled here are *relative* to the configured ``notes_root`` and are
validated so they can never escape it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

CONTEXT_FILENAME = "AGENTS.md"

_ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_FRONTMATTER_DATE_RE = re.compile(
    r"\A---\s*\n(.*?)\n---", re.DOTALL
)
_FRONTMATTER_DATE_LINE_RE = re.compile(
    r"^date:\s*[\"']?(\d{4}-\d{2}-\d{2})", re.MULTILINE
)


class NoteError(Exception):
    """A user-facing error in a notes operation (bad path, missing file...)."""


@dataclass
class TreeEntry:
    path: str  # relative to notes_root, posix-style
    name: str
    is_dir: bool
    has_context: bool = False  # dir contains an AGENTS.md


@dataclass
class SearchHit:
    path: str
    line_number: int
    line: str


@dataclass
class NoteStore:
    """All note operations, rooted at (and confined to) ``root``."""

    root: Path

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    # -- path safety ------------------------------------------------------

    def resolve(self, relative: str) -> Path:
        """Resolve a user-supplied relative path, refusing escapes from root."""
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise NoteError(f"path escapes notes root: {relative!r}")
        return candidate

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # -- tree -------------------------------------------------------------

    def list_dir(self, relative: str = "") -> list[TreeEntry]:
        """Entries of one directory, dirs first, alphabetical."""
        directory = self.resolve(relative)
        if not directory.is_dir():
            raise NoteError(f"not a directory: {relative!r}")
        entries = []
        for child in sorted(
            directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        ):
            if child.name.startswith("."):
                continue
            entries.append(
                TreeEntry(
                    path=self._relative(child),
                    name=child.name,
                    is_dir=child.is_dir(),
                    has_context=child.is_dir() and (child / CONTEXT_FILENAME).is_file(),
                )
            )
        return entries

    def list_folders(self) -> list[str]:
        """All folder paths in the tree (recursive), '' for the root, sorted."""
        folders = [""]
        for path in sorted(self.root.rglob("*")):
            if path.is_dir() and not any(
                part.startswith(".") for part in path.relative_to(self.root).parts
            ):
                folders.append(self._relative(path))
        return folders

    def iter_notes(self, subtree: str = "") -> list[Path]:
        """All note files under a subtree (excludes hidden files and AGENTS.md)."""
        base = self.resolve(subtree)
        if not base.is_dir():
            raise NoteError(f"not a directory: {subtree!r}")
        notes = []
        for path in sorted(base.rglob("*")):
            rel_parts = path.relative_to(self.root).parts
            if (
                path.is_file()
                and path.name != CONTEXT_FILENAME
                and not any(part.startswith(".") for part in rel_parts)
            ):
                notes.append(path)
        return notes

    # -- read / write -----------------------------------------------------

    def read(self, relative: str) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise NoteError(f"no such note: {relative!r}")
        return path.read_text(encoding="utf-8")

    def write(self, relative: str, content: str) -> str:
        """Write a note, creating parent directories. Returns the relative path."""
        if not relative or relative.endswith("/"):
            raise NoteError(f"not a valid note path: {relative!r}")
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return self._relative(path)

    # -- search -----------------------------------------------------------

    def search(
        self, terms: list[str], subtree: str = "", any_of: bool = True
    ) -> list[SearchHit]:
        """Case-insensitive full-text search over notes in a subtree.

        ``any_of=True``: a line matches if it contains any term. The returned
        hits contain at most one hit per matching line.
        """
        lowered = [t.lower() for t in terms if t]
        if not lowered:
            return []
        hits = []
        for path in self.iter_notes(subtree):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # skip binary-ish files
            for lineno, line in enumerate(text.splitlines(), start=1):
                low = line.lower()
                matched = (
                    any(t in low for t in lowered)
                    if any_of
                    else all(t in low for t in lowered)
                )
                if matched:
                    hits.append(
                        SearchHit(
                            path=self._relative(path),
                            line_number=lineno,
                            line=line.strip(),
                        )
                    )
        return hits

    # -- note dates (used by the query prefilter) --------------------------

    def note_date(self, path: Path) -> date:
        """Best-effort date of a note.

        Precedence: frontmatter ``date:`` → ISO date in the filename → mtime.
        """
        try:
            head = path.read_text(encoding="utf-8")[:2000]
        except UnicodeDecodeError:
            head = ""
        fm = _FRONTMATTER_DATE_RE.match(head)
        if fm:
            dm = _FRONTMATTER_DATE_LINE_RE.search(fm.group(1))
            if dm:
                try:
                    return date.fromisoformat(dm.group(1))
                except ValueError:
                    pass
        nm = _ISO_DATE_RE.search(path.name)
        if nm:
            try:
                return date.fromisoformat(nm.group(1))
            except ValueError:
                pass
        return datetime.fromtimestamp(path.stat().st_mtime).date()


@dataclass
class NoteInfo:
    """A note with its resolved date, as used by query prefiltering."""

    path: str
    note_date: date
    content: str = field(repr=False, default="")
