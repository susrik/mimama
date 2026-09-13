"""Hierarchical AGENTS.md context assembly.

For any path in the notes tree, the LLM context is the concatenation of the
``AGENTS.md`` files found while walking from the notes root *down* to the
target folder — root first, so general context precedes specific context.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from mimama.core.notes import CONTEXT_FILENAME, NoteStore


@dataclass
class ContextFile:
    """One AGENTS.md file found on the path from the notes root to a folder."""

    origin: str  # relative folder, "" for the notes root
    content: str


def list_context_files(store: NoteStore, relative: str) -> list[ContextFile]:
    """AGENTS.md files from root to ``relative`` (root-first), with origin + content.

    ``relative`` may name a folder or a file (its folder is used). Only
    non-empty AGENTS.md files are included.
    """
    target = store.resolve(relative)
    folder = target if target.is_dir() else target.parent

    rel_folder = folder.relative_to(store.root)
    chain = [PurePosixPath("")]
    for part in rel_folder.parts:
        chain.append(chain[-1] / part)

    files = []
    for rel in chain:
        context_file = store.root / rel / CONTEXT_FILENAME
        if context_file.is_file():
            content = context_file.read_text(encoding="utf-8").strip()
            if content:
                origin = str(rel) if str(rel) != "." else ""
                files.append(ContextFile(origin=origin, content=content))
    return files


def assemble_context(store: NoteStore, relative: str) -> str:
    """Concatenated AGENTS.md content from root to ``relative`` (root-first).

    Returns "" when no context files exist on the path.
    """
    blocks = [
        f"# Context from: {f.origin or '(notes root)'}\n\n{f.content}"
        for f in list_context_files(store, relative)
    ]
    return "\n\n---\n\n".join(blocks)
