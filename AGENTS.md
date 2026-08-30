# mimama — maintainer & agent context

LLM-assisted manager for a human-readable tree of markdown meeting notes.

## Architecture in one paragraph

The filesystem is the data model: notes are markdown files in a directory tree
(`notes_root` in config); folders group topics and may contain an `AGENTS.md`
whose content is LLM context, assembled hierarchically root-first for any
operation on files in that subtree. A single FastAPI server exposes a stable
user-facing JSON API under `/api` (all bodies typed by pydantic models in
`mimama/server/schemas.py`), a GUI-internal htmx surface under `/gui` (+ `/`),
and optionally an MCP streamable-HTTP endpoint at `/mcp`. The CLI, Discord
listener, and MCP tools are thin HTTP clients of `/api` — logic lives in
`mimama/core/`, never in the frontends.

## Package map

- `mimama/config.py` — pydantic `Config` + JSON loader. Server reads filename
  from `MIMAMA_CONFIG_FILENAME` (default `mimama.json`); CLI/stdio-MCP take
  `--config` with a click callback that surfaces pydantic errors.
- `mimama/core/notes.py` — tree listing, read/write, full-text search.
  ALL paths are validated relative to `notes_root`; never allow escapes.
- `mimama/core/context.py` — hierarchical AGENTS.md assembly.
- `mimama/core/llm.py` — `LLMClient` wrapping the openai SDK against any
  OpenAI-protocol endpoint (base_url + api_key, e.g. litellm).
- `mimama/core/operations.py` — transcript→minutes, notes cleanup.
- `mimama/core/query.py` — natural-language query pipeline:
  (1) LLM plans a structured `QueryPlan` (keywords, date range, subtree,
  semantic question); (2) mechanical prefilter (dates: frontmatter →
  ISO-in-filename → mtime; keyword grep); (3) optional LLM semantic rerank of
  capped excerpts. Token cost is bounded by `query.max_candidates` /
  `query.excerpt_chars` config.
- `mimama/server/` — `app.py` (factory: mounts routers, conditionally mounts
  MCP, starts enabled listeners), `schemas.py`, `api.py`, `gui.py`, templates.
- `mimama/cli/main.py` — click CLI; any missing option is prompted for with
  guided flows (numbered folder pickers fetched from /api, `click.edit()` for
  transcripts, confirm-before-send).
- `mimama/listeners/` — `base.py` defines the platform-agnostic `Listener`
  protocol and `IncomingMessage`/`Reply`; `router.py` parses commands and
  calls `/api`; `discord.py` implements the protocol. To add Slack: new config
  sub-model + new module implementing the same protocol reusing `router.py`.
- `mimama/mcp/` — `tools.py` defines all MCP tools ONCE (thin `/api` clients);
  `server.py` exposes them via stdio (`mimama-mcp` entry point) and via a
  streamable-HTTP ASGI app mounted at `/mcp` when `mcp.http_enabled`.

## Conventions & invariants

- Python ≥ 3.12, flat package layout (no `src/`), pip + setuptools.
- ALL tool config lives in `pyproject.toml` (ruff, mypy strict, pytest,
  coverage `fail_under = 95`). Run: `pytest`, `mypy mimama`, `ruff check .`.
- Every `/api` request/response body has a pydantic model in `schemas.py`;
  frontends import those same models — one typed contract.
- uvicorn is NOT a project dependency; it is installed in the Docker image
  (see Dockerfile) or ad hoc for local runs.
- Tests never call a real LLM: inject `FakeLLMClient` for logic tests; a few
  respx contract tests cover `llm.py`'s HTTP wiring.
- No database, no auth (trusted-network tool), no JS build step (tailwind +
  daisyui via CDN), no job queue. Keep it that way unless scale demands it.

## Deferred / future options

- Audio → text transcription (inputs are currently pre-created text).
- Slack listener (protocol is ready).
- Embeddings/vector index for the query prefilter, only if keyword filtering
  proves too weak on large trees; the litellm endpoint can serve embeddings
  without architectural change.
