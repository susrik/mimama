"""LLM-backed note operations: transcript → minutes, notes cleanup.

Both operations assemble hierarchical AGENTS.md context for the target folder
and pass it to the LLM as system context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath

from mimama.core.context import assemble_context
from mimama.core.llm import SupportsComplete
from mimama.core.notes import NoteStore

_BASE_SYSTEM = """\
You are an assistant that manages a tree of markdown meeting notes.
Output ONLY the markdown document, no surrounding commentary or code fences.
"""

_CONVERT_INSTRUCTIONS = """\
Convert the following raw meeting transcript into well-structured meeting
minutes in markdown. Include: a title, the date if known, attendees if
identifiable, a concise summary, key discussion points, decisions, and action
items (with owners where identifiable).
"""

_CLEANUP_INSTRUCTIONS = """\
Clean up the following rough meeting notes into polished, shareable meeting
minutes in markdown. Preserve all factual content, fix structure, spelling and
clarity; do not invent information that is not present.
"""


@dataclass
class OperationResult:
    path: str  # relative path of the written note
    content: str  # generated markdown


def _system_prompt(store: NoteStore, folder: str) -> str:
    context = assemble_context(store, folder)
    if context:
        return f"{_BASE_SYSTEM}\nContext for this folder (general → specific):\n\n{context}"
    return _BASE_SYSTEM


def default_minutes_filename(today: date | None = None) -> str:
    return f"{(today or date.today()).isoformat()}-minutes.md"


def transcript_to_minutes(
    store: NoteStore,
    llm: SupportsComplete,
    folder: str,
    transcript: str,
    filename: str | None = None,
) -> OperationResult:
    """Convert a raw transcript to meeting minutes, written into ``folder``."""
    filename = filename or default_minutes_filename()
    target = (PurePosixPath(folder) / filename).as_posix()
    content = llm.complete(
        system=_system_prompt(store, folder),
        user=f"{_CONVERT_INSTRUCTIONS}\n--- TRANSCRIPT ---\n{transcript}",
    )
    written = store.write(target, content)
    return OperationResult(path=written, content=content)


def cleanup_note(
    store: NoteStore,
    llm: SupportsComplete,
    path: str,
) -> OperationResult:
    """Clean up an existing rough note in place."""
    original = store.read(path)
    content = llm.complete(
        system=_system_prompt(store, path),
        user=f"{_CLEANUP_INSTRUCTIONS}\n--- NOTES ---\n{original}",
    )
    written = store.write(path, content)
    return OperationResult(path=written, content=content)
