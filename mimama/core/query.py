"""Natural-language query pipeline over the notes tree.

Three stages, designed to keep LLM token cost bounded:

1. **Plan** — one small LLM call turns the user's question (plus today's date
   and the configured user identity) into a structured :class:`QueryPlan`:
   keywords, absolute date range, optional subtree, optional semantic
   question. Malformed LLM output is retried once, then fails cleanly.
2. **Prefilter** — mechanical, zero-token: date filter (frontmatter date →
   ISO date in filename → mtime) plus case-insensitive keyword matching
   produces a small candidate set.
3. **Rerank** — only when the plan contains a semantic question: capped,
   keyword-centred excerpts of the candidates go to the LLM, which selects
   and justifies the relevant notes.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, Field, ValidationError

from mimama.config import QueryConfig, UserConfig
from mimama.core.llm import SupportsComplete
from mimama.core.notes import NoteError, NoteInfo, NoteStore


def _noop(_chunk: str) -> None:
    return None


class QueryError(Exception):
    """The query could not be planned or executed."""


class QueryPlan(BaseModel):
    """Structured search plan produced by the LLM from a natural query."""

    keywords: list[str] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    subtree: str | None = None
    semantic_question: str | None = None


@dataclass
class QueryResult:
    path: str
    note_date: date
    snippet: str
    reason: str = ""


_PLAN_SYSTEM = """\
You translate a natural-language question about a personal tree of markdown
meeting notes into a JSON search plan. Respond with ONLY a JSON object (no
code fences, no commentary) with these keys:

- "keywords": array of strings — case-insensitive terms to grep for; include
  obvious variants/synonyms. Empty array if the query has no term filter.
- "date_from": "YYYY-MM-DD" or null — resolve relative expressions ("last 2
  days", "a week or two ago") against today's date; widen fuzzy ranges
  generously (e.g. "a week or two ago" -> from 21 days ago to 5 days ago).
- "date_to": "YYYY-MM-DD" or null.
- "subtree": relative folder path or null — only if the query clearly names a
  folder/project area you cannot express as keywords.
- "semantic_question": string or null — the judgement to apply to candidate
  notes (e.g. "which meeting decided X?"). null if pure keyword/date filter.

If the query says "me"/"I", use the user's name and aliases as keywords.
"""

_RERANK_SYSTEM = """\
You select which candidate notes answer the user's question. Respond with
ONLY a JSON array (no code fences) of objects: {"path": "<candidate path>",
"reason": "<one line why it matches>"} — most relevant first, only genuinely
relevant notes, empty array if none match.
"""

_JSON_BLOCK_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def _extract_json(text: str) -> str:
    """Best effort: pull the JSON object/array out of an LLM reply."""
    match = _JSON_BLOCK_RE.search(text)
    return match.group(0) if match else text


def plan_query(
    llm: SupportsComplete,
    question: str,
    user: UserConfig,
    today: date | None = None,
    on_chunk: Callable[[str], None] = _noop,
    cancel_event: threading.Event | None = None,
) -> QueryPlan:
    """Stage 1: one LLM call producing a validated QueryPlan (1 retry)."""
    today = today or date.today()
    user_desc = (
        f'The user is "{user.name}" (aliases: {", ".join(user.aliases)}).'
        if user.name
        else "The user's name is not configured."
    )
    prompt = f"Today is {today.isoformat()}. {user_desc}\n\nQuestion: {question}"
    last_error: Exception | None = None
    for _ in range(2):
        raw = llm.stream_complete(
            system=_PLAN_SYSTEM, user=prompt, on_chunk=on_chunk, cancel_event=cancel_event
        )
        try:
            return QueryPlan.model_validate_json(_extract_json(raw))
        except ValidationError as exc:
            last_error = exc
    raise QueryError(f"LLM failed to produce a valid query plan: {last_error}")


def prefilter(store: NoteStore, plan: QueryPlan) -> list[NoteInfo]:
    """Stage 2: mechanical date + keyword filter. No LLM involved."""
    subtree = plan.subtree or ""
    try:
        paths = store.iter_notes(subtree)
    except NoteError:
        paths = store.iter_notes("")  # bad subtree from the LLM: fall back to root
    lowered = [k.lower() for k in plan.keywords if k]
    candidates = []
    for path in paths:
        note_date = store.note_date(path)
        if plan.date_from and note_date < plan.date_from:
            continue
        if plan.date_to and note_date > plan.date_to:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if lowered and not any(k in content.lower() for k in lowered):
            continue
        candidates.append(
            NoteInfo(
                path=path.relative_to(store.root).as_posix(),
                note_date=note_date,
                content=content,
            )
        )
    candidates.sort(key=lambda n: n.note_date, reverse=True)
    return candidates


def _excerpt(content: str, keywords: list[str], max_chars: int) -> str:
    """Excerpt centred on the first keyword hit (or the head of the note)."""
    if len(content) <= max_chars:
        return content
    low = content.lower()
    pos = 0
    for keyword in keywords:
        found = low.find(keyword.lower())
        if found != -1:
            pos = found
            break
    start = max(0, pos - max_chars // 4)
    excerpt = content[start : start + max_chars]
    prefix = "..." if start > 0 else ""
    suffix = "..." if start + max_chars < len(content) else ""
    return f"{prefix}{excerpt}{suffix}"


class _RerankChoice(BaseModel):
    path: str
    reason: str = ""


def rerank(
    llm: SupportsComplete,
    question: str,
    candidates: list[NoteInfo],
    keywords: list[str],
    settings: QueryConfig,
    on_chunk: Callable[[str], None] = _noop,
    cancel_event: threading.Event | None = None,
) -> list[tuple[NoteInfo, str]]:
    """Stage 3: LLM selects/justifies relevant candidates from capped excerpts."""
    capped = candidates[: settings.max_candidates]
    by_path = {c.path: c for c in capped}
    sections = [
        f"### {c.path} (date: {c.note_date.isoformat()})\n"
        f"{_excerpt(c.content, keywords, settings.excerpt_chars)}"
        for c in capped
    ]
    prompt = f"Question: {question}\n\nCandidate notes:\n\n" + "\n\n".join(sections)
    last_error: Exception | None = None
    for _ in range(2):
        raw = llm.stream_complete(
            system=_RERANK_SYSTEM, user=prompt, on_chunk=on_chunk, cancel_event=cancel_event
        )
        try:
            items = json.loads(_extract_json(raw))
            choices = [_RerankChoice.model_validate(item) for item in items]
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            last_error = exc
            continue
        return [
            (by_path[c.path], c.reason) for c in choices if c.path in by_path
        ]
    raise QueryError(f"LLM failed to produce a valid rerank result: {last_error}")


def _snippet(content: str, keywords: list[str], width: int = 200) -> str:
    return _excerpt(content, keywords, width).replace("\n", " ").strip()


def run_query(
    store: NoteStore,
    llm: SupportsComplete,
    question: str,
    user: UserConfig,
    settings: QueryConfig,
    today: date | None = None,
    on_chunk: Callable[[str], None] = _noop,
    cancel_event: threading.Event | None = None,
) -> tuple[QueryPlan, list[QueryResult]]:
    """Full pipeline. Returns the plan (for transparency) and ranked results."""
    plan = plan_query(
        llm, question, user, today=today, on_chunk=on_chunk, cancel_event=cancel_event
    )
    candidates = prefilter(store, plan)
    if plan.semantic_question and candidates:
        ranked = rerank(
            llm,
            plan.semantic_question,
            candidates,
            plan.keywords,
            settings,
            on_chunk=on_chunk,
            cancel_event=cancel_event,
        )
        results = [
            QueryResult(
                path=info.path,
                note_date=info.note_date,
                snippet=_snippet(info.content, plan.keywords),
                reason=reason,
            )
            for info, reason in ranked
        ]
    else:
        results = [
            QueryResult(
                path=info.path,
                note_date=info.note_date,
                snippet=_snippet(info.content, plan.keywords),
            )
            for info in candidates[: settings.max_candidates]
        ]
    return plan, results
