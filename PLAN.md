# mimama — Build Plan

LLM-assisted manager for a human-readable tree of markdown meeting notes.

## Overview

One FastAPI server; htmx web UI, click CLI, Discord bot, and MCP server are all
HTTP clients of it. LLM operations (transcript → minutes, cleanup notes,
natural-language query) assemble hierarchical context from `AGENTS.md` files
walking from the note's folder up to the notes root (concatenated root-first:
general → specific).

## Layout

```
mimama/                       # repo root
├── pyproject.toml            # ruff, mypy, pytest, coverage config; NO uvicorn dep
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── mimama.example.json
├── AGENTS.md                 # maintainer/agent context
├── PLAN.md                   # this plan
├── docs/setup.md             # app + MCP setup (local & remote)
├── mimama/                   # flat package (no src/)
│   ├── config.py             # pydantic Config model + JSON loader + click callback
│   ├── core/
│   │   ├── notes.py          # tree ops, search, path safety (no escaping notes root)
│   │   ├── context.py        # hierarchical AGENTS.md assembly
│   │   ├── llm.py            # LLMClient over openai SDK (base_url + key → litellm etc.)
│   │   ├── operations.py     # transcript_to_minutes(), cleanup_notes()
│   │   └── query.py          # LLM-powered smart query pipeline
│   ├── server/
│   │   ├── app.py            # FastAPI app factory; mounts routers; starts enabled listeners
│   │   ├── schemas.py        # pydantic models for ALL API request/response bodies
│   │   ├── api.py            # /api — user-facing JSON API (CLI, listeners, MCP, external)
│   │   └── gui.py            # /gui + / — htmx pages & fragments, Jinja2, tailwind+daisyui CDN
│   ├── cli/main.py           # click CLI (guided prompts), httpx client to /api
│   ├── listeners/
│   │   ├── base.py           # generic Listener protocol (platform-agnostic)
│   │   ├── router.py         # shared command parse/dispatch → /api HTTP calls
│   │   └── discord.py        # discord.py bot implementing the protocol
│   └── mcp/
│       ├── tools.py          # MCP tool definitions (single source, both transports)
│       └── server.py         # stdio entry point + streamable-HTTP ASGI app
└── tests/                    # mirrors package layout
```

## API separation

- **`/api`** — user-facing, stable JSON API consumed by CLI, listeners, MCP,
  and any external client. All request/response bodies are pydantic models in
  `server/schemas.py` (no raw dicts).
- **`/gui`** — GUI-internal endpoints serving htmx HTML fragments and form
  handlers; free to change with the UI. `/` serves the main page. GUI handlers
  call core functions directly (no HTTP round-trips to /api).
- **`/mcp`** — streamable-HTTP MCP endpoint, mounted only when
  `mcp.http_enabled` is true in config.

## Config

- Pydantic `Config` model parsed from a JSON file. Typed sub-models for `llm`,
  `server`, `user` (name/aliases for "mentioning me" queries), per-listener
  settings (each with an `enabled` flag), and `mcp` (`http_enabled`).
- **Server/Docker**: filename defaults to `mimama.json`, overridable via the
  `MIMAMA_CONFIG_FILENAME` env var.
- **CLI / stdio MCP**: `--config` option (default `mimama.json`) with a click
  validation callback that pydantic-parses the file and raises
  `click.BadParameter` (showing pydantic's messages) on failure.

## Smart query pipeline (core/query.py)

Natural-language queries (e.g. "all documents mentioning EuroHPC in the last 2
days", "find the notes where we decided X") run server-side, token-efficient:

1. **Query planning** (1 small LLM call): user query + today's date + user
   name/aliases → structured `QueryPlan` (keywords, date_from/date_to,
   subtree, semantic_question) via JSON structured output. Malformed output →
   one retry, then clean error.
2. **Mechanical prefilter** (no LLM): date filter (frontmatter date →
   filename ISO date → file mtime, in that precedence) + case-insensitive
   keyword match → small candidate set with snippets.
3. **Semantic rerank** (LLM, only if `semantic_question` set): capped,
   keyword-centred excerpts of candidates → ranked results with one-line
   justifications. Bounded by `max_candidates` / excerpt-size limits.

Plain `/api/search` (mechanical full-text) stays for cheap exact lookups.
Deferred: embeddings/vector index (only if keyword prefilter proves too weak).

## MCP

- Tool definitions once in `mimama/mcp/tools.py`; thin HTTP clients of `/api`.
- Tools: list_notes, read_note, add_note, search_notes, query_notes (headline
  natural-language tool), convert_transcript, cleanup_note, get_context.
- Transports: stdio via `mimama-mcp` console script (client-spawned), and
  streamable HTTP mounted at `/mcp` when `mcp.http_enabled`.

## CLI

click-based, hybrid interactive: every parameter is a click option for
scripting, but missing parameters are prompted for with guided flows
(tree-fetched numbered folder pickers, `click.edit()` for transcript entry,
summary + confirm before sending). Prompted values validated against the
schemas models; validation errors re-prompt.

## Listeners

Generic `Listener` protocol + shared command router (parse/dispatch → /api).
Discord fully implemented in v1 (discord.py); Slack later is a drop-in
(new sub-model + listener class).

## Docker

- Dockerfile: python slim; `pip install .` + `pip install uvicorn` (uvicorn is
  image-only, never in pyproject.toml). CMD: uvicorn mimama.server.app.
- docker-compose.yml: mounts ./mimama.json read-only, sets
  MIMAMA_CONFIG_FILENAME, maps port 8000, mounts a notes volume.

## Testing

- pytest + pytest-cov (fail_under = 95), pytest-asyncio, FastAPI TestClient.
- LLM mocking: injected `FakeLLMClient` for most tests; a few respx contract
  tests for llm.py HTTP wiring. Never hit a real LLM in tests.
- mypy + ruff; all tool config in pyproject.toml only.

## Scope decisions

- Input = pre-created text transcripts (audio/STT later).
- No database (filesystem is the data model), no auth (trusted-network tool),
  no JS build step, no async job queue.

## Build order

1. Scaffold: pyproject.toml, requirements.txt, package skeleton, Docker files,
   mimama.example.json, PLAN.md, AGENTS.md, docs/setup.md stub
2. config.py + tests
3. core/notes.py (incl. search) + core/context.py + tests
4. core/llm.py + core/operations.py + tests
5. core/query.py + tests
6. server/schemas.py + /api (incl. /api/search, /api/query) + tests
7. /gui htmx UI + tests
8. Guided click CLI + tests
9. Listener protocol + router + Discord bot + tests
10. MCP tools + stdio entry + HTTP mount (config-gated) + tests
11. Final pass: mypy clean, ruff clean, coverage ≥ 95%, docker build verified,
    docs/setup.md + AGENTS.md finalized
