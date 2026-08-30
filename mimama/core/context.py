"""Hierarchical AGENTS.md context assembly.

For any path in the notes tree, the LLM context is the concatenation of the
``AGENTS.md`` files found while walking from the notes root *down* to the
target folder — root first, so general context precedes specific context.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from mimama.core.notes import CONTEXT_FILENAME, NoteStore


def assemble_context(store: NoteStore, relative: str) -> str:
    """Concatenated AGENTS.md content from root to ``relative`` (root-first).

    ``relative`` may name a folder or a file (its folder is used). Each block
    is prefixed with a header naming its origin folder. Returns "" when no
    context files exist on the path.
    """
    target = store.resolve(relative)
    folder = target if target.is_dir() else target.parent

    rel_folder = folder.relative_to(store.root)
    chain = [PurePosixPath("")]
    for part in rel_folder.parts:
        chain.append(chain[-1] / part)

    blocks = []
    for rel in chain:
        context_file = store.root / rel / CONTEXT_FILENAME
        if context_file.is_file():
            origin = str(rel) if str(rel) != "." else "(notes root)"
            content = context_file.read_text(encoding="utf-8").strip()
            if content:
                blocks.append(f"# Context from: {origin or '(notes root)'}\n\n{content}")
    return "\n\n---\n\n".join(blocks)
