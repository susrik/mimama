"""LLM-backed note operations: transcript → minutes, notes cleanup.

Both operations assemble hierarchical AGENTS.md context for the target folder
and pass it to the LLM as system context.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath

from mimama.core.context import assemble_context
from mimama.core.llm import SupportsComplete
from mimama.core.notes import NoteStore


def _noop(_chunk: str) -> None:
    return None

_BASE_SYSTEM = """\
You are an assistant that manages a tree of markdown meeting notes.
Output ONLY the markdown document, no surrounding commentary or code fences.
"""

DEFAULT_TRANSCRIPT_INSTRUCTIONS = """\
Read the following transcript and produce meeting minutes, noting important \
topics, open questions, action items and other highlights.\
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
    instructions: str | None = None,
    context: str | None = None,
) -> OperationResult:
    """Convert a raw transcript to meeting minutes, written into ``folder``.

    ``instructions`` is the (editable) prompt describing what to do with the
    transcript; it defaults to :data:`DEFAULT_TRANSCRIPT_INSTRUCTIONS`.
    ``context`` is optional free-form context prepended to the instructions.
    """
    filename = filename or default_minutes_filename()
    target = (PurePosixPath(folder) / filename).as_posix()
    instructions = instructions or DEFAULT_TRANSCRIPT_INSTRUCTIONS
    user = f"{context}\n\n{instructions}" if context else instructions
    content = llm.complete(
        system=_system_prompt(store, folder),
        user=f"{user}\n--- TRANSCRIPT ---\n{transcript}",
    )
    written = store.write(target, content)
    return OperationResult(path=written, content=content)


def generate_note_content(
    store: NoteStore,
    llm: SupportsComplete,
    path: str,
    context: str,
    content: str,
    on_chunk: Callable[[str], None] = _noop,
    cancel_event: threading.Event | None = None,
) -> str:
    """Run a free-form AI generation for the "new file" flow.

    ``context`` and ``content`` (the user-supplied "context" and "content"
    fields from the new-file dialog) become the prompt; ``path``'s folder
    supplies the hierarchical AGENTS.md system context. ``on_chunk``/
    ``cancel_event`` let callers stream the response and cancel mid-flight
    (see ``mimama.core.llm.LLMClient.stream_complete``).
    """
    user = f"{context}\n\n{content}".strip() if context else content
    return llm.stream_complete(
        system=_system_prompt(store, path), user=user, on_chunk=on_chunk, cancel_event=cancel_event
    )


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
