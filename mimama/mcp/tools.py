"""MCP tool definitions — defined ONCE, exposed over stdio and streamable HTTP.

Every tool is a thin HTTP client of the mimama server's /api, via the shared
:class:`mimama.api_client.APIClient`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mimama.api_client import APIClient

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer


def register_tools(mcp_server: MCPServer, api: APIClient) -> None:
    """Register all mimama tools on an MCPServer instance."""
    tool = mcp_server.tool

    @tool()
    def list_notes(path: str = "") -> str:
        """List the contents of a folder in the notes tree.

        Args:
            path: folder path relative to the notes root ("" = root).
        Returns one entry per line: "[dir]" or "[note]" plus the relative path.
        """
        response = api.tree(path)
        lines = [
            f"[{'dir' if e.is_dir else 'note'}] {e.path}" for e in response.entries
        ]
        return "\n".join(lines) or "(empty folder)"

    @tool()
    def read_note(path: str) -> str:
        """Read the full content of one note.

        Args:
            path: note path relative to the notes root.
        """
        return api.read_note(path).content

    @tool()
    def add_note(path: str, content: str) -> str:
        """Create or overwrite a note (markdown). Parent folders are created.

        Args:
            path: note path relative to the notes root, e.g. "projects/x/2026-08-30-sync.md".
            content: full markdown content of the note.
        """
        return f"saved: {api.write_note(path, content).path}"

    @tool()
    def search_notes(terms: list[str], subtree: str = "") -> str:
        """Cheap exact full-text search (case-insensitive, no LLM).

        Use query_notes instead for natural-language or date-based questions.

        Args:
            terms: literal terms; a line matches if it contains any of them.
            subtree: optional folder to restrict the search to.
        """
        response = api.search(terms, subtree=subtree)
        lines = [f"{h.path}:{h.line_number}: {h.line}" for h in response.hits]
        return "\n".join(lines) or "(no matches)"

    @tool()
    def query_notes(question: str) -> str:
        """Answer a natural-language question about the notes (LLM-powered).

        Pass the user's question verbatim — it understands dates ("in the
        last 2 days", "a week or two ago"), people ("mentioning me"), and
        semantic criteria ("the meeting where we decided X"). Returns ranked
        matching notes with dates, snippets, and reasons.

        Args:
            question: the natural-language question, verbatim.
        """
        response = api.query(question)
        if not response.results:
            return "(no matching notes)"
        lines = []
        for r in response.results:
            line = f"{r.path} ({r.note_date})"
            if r.reason:
                line += f" — {r.reason}"
            lines.append(line + f"\n    {r.snippet}")
        return "\n".join(lines)

    @tool()
    def convert_transcript(
        folder: str, transcript: str, filename: str = ""
    ) -> str:
        """Convert a raw meeting transcript into meeting minutes (LLM-powered).

        The minutes are generated using the folder's hierarchical AGENTS.md
        context and saved into the folder.

        Args:
            folder: target folder relative to the notes root.
            transcript: the raw transcript text.
            filename: optional output filename (default: dated, e.g. 2026-08-30-minutes.md).
        """
        result = api.convert(folder, transcript, filename=filename or None)
        return f"saved: {result.path}\n\n{result.content}"

    @tool()
    def cleanup_note(path: str) -> str:
        """Clean up a rough note into polished minutes, in place (LLM-powered).

        Args:
            path: note path relative to the notes root.
        """
        result = api.cleanup(path)
        return f"saved: {result.path}\n\n{result.content}"

    @tool()
    def get_context(path: str = "") -> str:
        """Show the assembled hierarchical AGENTS.md context for a folder.

        Args:
            path: folder (or note) path relative to the notes root.
        """
        return api.context(path).context or "(no context files on this path)"
