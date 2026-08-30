"""Shared command router for chat listeners.

Parses simple text commands from any platform and dispatches them to the
mimama server via the shared :class:`APIClient`. Platform listeners
(Discord today, Slack later) construct one router and feed it
:class:`IncomingMessage` objects.

Commands:
    !help                      — usage
    !ls [folder]               — list a folder
    !show <path>               — show a note
    !search <term> [term...]   — exact full-text search
    !query <question>          — LLM-powered natural-language query
    !cleanup <path>            — clean up a note (LLM)
    !convert <folder>          — convert the rest of the message (after a
                                 newline) as a transcript into minutes
Anything not starting with "!" is ignored (returns None).
"""

from __future__ import annotations

from mimama.api_client import APIClient, APIError
from mimama.listeners.base import IncomingMessage, Reply

HELP_TEXT = """\
mimama commands:
  !ls [folder]              list a folder
  !show <path>              show a note
  !search <term> [term...]  exact full-text search
  !query <question>         natural-language query (LLM)
  !cleanup <path>           clean up a note (LLM)
  !convert <folder>         next lines = transcript, converted to minutes
  !help                     this text
"""

_MAX_REPLY = 1800  # keep well under Discord's 2000-char message limit


class CommandRouter:
    def __init__(self, api: APIClient) -> None:
        self._api = api

    def handle(self, message: IncomingMessage) -> Reply | None:
        """Route one message; None means "not for us"."""
        text = message.text.strip()
        if not text.startswith("!"):
            return None
        first_line, _, rest = text.partition("\n")
        parts = first_line.split()
        command, args = parts[0].lower(), parts[1:]
        try:
            return self._dispatch(command, args, rest.strip())
        except APIError as exc:
            return Reply(text=f"error: {exc}")

    def _dispatch(self, command: str, args: list[str], body: str) -> Reply | None:
        if command == "!help":
            return Reply(text=HELP_TEXT)
        if command == "!ls":
            return self._ls("" if not args else args[0])
        if command == "!show":
            if not args:
                return Reply(text="usage: !show <path>")
            return self._clip(self._api.read_note(args[0]).content)
        if command == "!search":
            if not args:
                return Reply(text="usage: !search <term> [term...]")
            return self._search(args)
        if command == "!query":
            if not args:
                return Reply(text="usage: !query <question>")
            return self._query(" ".join(args))
        if command == "!cleanup":
            if not args:
                return Reply(text="usage: !cleanup <path>")
            result = self._api.cleanup(args[0])
            return self._clip(f"saved: {result.path}\n{result.content}")
        if command == "!convert":
            if not args or not body:
                return Reply(
                    text="usage: !convert <folder>\\n<transcript on following lines>"
                )
            result = self._api.convert(args[0], body)
            return self._clip(f"saved: {result.path}\n{result.content}")
        return Reply(text=f"unknown command {command} — try !help")

    def _ls(self, path: str) -> Reply:
        entries = self._api.tree(path).entries
        lines = [
            f"{'📁' if e.is_dir else '📄'} {e.path}" for e in entries
        ]
        return self._clip("\n".join(lines) or "(empty folder)")

    def _search(self, terms: list[str]) -> Reply:
        hits = self._api.search(terms).hits
        lines = [f"{h.path}:{h.line_number}: {h.line}" for h in hits]
        return self._clip("\n".join(lines) or "(no matches)")

    def _query(self, question: str) -> Reply:
        results = self._api.query(question).results
        if not results:
            return Reply(text="(no matching notes)")
        lines = []
        for r in results:
            line = f"{r.path} ({r.note_date})"
            if r.reason:
                line += f" — {r.reason}"
            lines.append(line)
        return self._clip("\n".join(lines))

    @staticmethod
    def _clip(text: str) -> Reply:
        if len(text) > _MAX_REPLY:
            text = text[: _MAX_REPLY - 1] + "…"
        return Reply(text=text)
