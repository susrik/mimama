"""Hierarchical AGENTS.md context assembly.

For any path in the notes tree, the LLM context is the concatenation of the
``AGENTS.md`` files found while walking from the notes root *down* to the
target folder — root first, so general context precedes specific context.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mimama.core.documents import DocumentError, extract_text, is_document
from mimama.core.notes import CONTEXT_FILENAME, NoteStore


class FileContextError(Exception):
    """A server-side file selected for AI context could not be read."""


@dataclass
class ContextFile:
    """One AGENTS.md file found on the path from the notes root to a folder."""

    origin: str  # relative folder, "" for the notes root
    content: str


@dataclass
class AttachedFile:
    """One local file added to the AI inference context (by upload or path).

    ``name`` is only for labeling the file in the assembled prompt; it is
    never used as a filesystem path on the server.
    """

    name: str
    content: str


def read_files(paths: list[str], max_chars: int | None = None) -> list[AttachedFile]:
    """Read arbitrary server files into :class:`AttachedFile` instances.

    Paths may be absolute or relative to the server's working directory. Text
    files are decoded as UTF-8; supported binary documents (``.pptx``,
    ``.docx``, ``.pdf``, ``.xlsx``, ``.doc``) have their text extracted.
    Raises :class:`FileContextError` if a path is missing, unreadable as text
    or an unknown document type, or exceeds ``max_chars``.
    """
    attached = []
    for path in paths:
        candidate = Path(path)
        if not candidate.is_file():
            raise FileContextError(f"no such file: {path!r}")
        data = candidate.read_bytes()
        attached.append(decode_attachment(candidate.name, data, max_chars))
    return attached


def decode_attachment(name: str, data: bytes, max_chars: int | None = None) -> AttachedFile:
    """Decode one attachment's raw bytes into an :class:`AttachedFile`.

    Supported binary documents (by extension) have their text extracted;
    anything else is treated as UTF-8 text. Raises :class:`FileContextError`
    for binary content that is neither a supported document nor valid UTF-8,
    for documents with no extractable text, and for content exceeding
    ``max_chars``.
    """
    if is_document(name):
        try:
            content = extract_text(name, data)
        except DocumentError as exc:
            raise FileContextError(str(exc)) from exc
        if not content.strip():
            raise FileContextError(f"no extractable text in {name!r}")
    else:
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FileContextError(f"not a text file: {name!r}") from exc
    if max_chars is not None and len(content) > max_chars:
        raise FileContextError(
            f"attached file too large: {name!r} ({len(content)} chars > {max_chars})"
        )
    return AttachedFile(name=name, content=content)


def format_files_context(files: list[AttachedFile]) -> str:
    """Render attached files as a labelled context block ("" when empty)."""
    if not files:
        return ""
    blocks = [f"# Attached file: {f.name}\n\n{f.content.strip()}" for f in files]
    return "\n\n---\n\n".join(blocks)


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
