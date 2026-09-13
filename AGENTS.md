# mimama — maintainer & agent context

LLM-assisted manager for a human-readable tree of markdown meeting notes.

## Architecture in one paragraph

The filesystem is the data model: notes are markdown files in a directory tree
(`notes.root` in config); folders group topics and may contain an `AGENTS.md`
whose content is LLM context, assembled hierarchically root-first for any
operation on files in that subtree. A single FastAPI server exposes a stable
user-facing JSON API under `/api` (all bodies typed by pydantic models in
`mimama/server/schemas.py`), a GUI-internal htmx surface under `/gui` (+ `/`),
and optionally an MCP streamable-HTTP endpoint at `/mcp`. The CLI, Discord
listener, and MCP tools are thin HTTP clients of `/api` — logic lives in
`mimama/core/`, never in the frontends. When `notes.git` is enabled, every
note mutation (write/delete/rename/create/convert/cleanup, from both `/api`
and `/gui`) is staged (never committed) via `mimama/core/git_ops.py`.

## Package map

- `mimama/config.py` — pydantic `Config` + JSON loader. Server reads filename
  from `MIMAMA_CONFIG_FILENAME` (default `mimama.json`); CLI/stdio-MCP take
  `--config` with a click callback that surfaces pydantic errors.
- `mimama/core/notes.py` — tree listing, read/write, full-text search.
  ALL paths are validated relative to `notes.root`; never allow escapes.
- `mimama/core/context.py` — hierarchical AGENTS.md assembly
  (`assemble_context`) plus a structured `list_context_files` used by the GUI
  to show which AGENTS.md files will be used, with per-file hover tooltips.
- `mimama/core/naming.py` — `default_note_filename()`: dated, Heroku-style
  default filename (`YYYY-MM-DD-adjective-noun.md`) for the "new file" GUI flow.
- `mimama/core/llm.py` — `LLMClient` wrapping the openai SDK against any
  OpenAI-protocol endpoint (base_url + api_key, e.g. litellm).
- `mimama/core/operations.py` — transcript→minutes, notes cleanup, and
  free-form AI generation (`generate_note_content`) for the "new file" flow.
- `mimama/core/git_ops.py` — the single seam that hides *how* git is invoked
  (shell `git` today; a future swap to a Python git library only touches this
  module). `stage_file`/`stage_files` run in `notes.root`; no-ops when
  `notes.git` is false. mimama only ever stages — it never commits or inits.
- `mimama/core/transcribe.py` — audio→text via ffmpeg (m4a→wav) + whisper-cli.
  Two entry points: `run_transcription_pipeline` (synchronous, path-based; used
  by the stable `/api/transcribe` endpoint) and `TranscriptionJob` /
  `TranscriptionJobRegistry` (background-thread, upload-based; used by the
  `/gui` new-file flow to stream live console output over SSE).
- `mimama/core/query.py` — natural-language query pipeline:
  (1) LLM plans a structured `QueryPlan` (keywords, date range, subtree,
  semantic question); (2) mechanical prefilter (dates: frontmatter →
  ISO-in-filename → mtime; keyword grep); (3) optional LLM semantic rerank of
  capped excerpts. Token cost is bounded by `query.max_candidates` /
  `query.excerpt_chars` config.
- `mimama/server/` — `app.py` (factory: mounts routers, conditionally mounts
  MCP, starts enabled listeners; owns the `TranscriptionJobRegistry`),
  `schemas.py`, `api.py`, `gui.py`, templates.
  - `/gui`'s "New file" flow (`GET`/`POST /gui/note/create`, template
    `new_note.html`) is the single entry point for creating notes: plain
    text, free-form AI generation, or transcript→minutes conversion (with an
    optional audio-upload sub-flow streamed over SSE from
    `/gui/transcribe/start` + `/gui/transcribe/stream/{job_id}`). It replaces
    what used to be three separate GUI flows ("new file", "convert
    transcript", "transcribe audio"); `/api/operations/convert` and
    `/api/transcribe` remain as the stable, path-based external contract for
    the CLI/MCP/listeners.
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
  frontends import those same models — one typed contract. `/gui` renders
  HTML fragments directly (Jinja2 templates) and is free to change with the UI.
- uvicorn is NOT a project dependency; it is installed in the Docker image
  (see Dockerfile) or ad hoc for local runs.
- Tests never call a real LLM: inject `FakeLLMClient` for logic tests; a few
  respx contract tests cover `llm.py`'s HTTP wiring. Similarly, tests never
  shell out to real `git`/`ffmpeg`/`whisper-cli` binaries for logic — either a
  scratch `git init`-ed temp repo (git_ops) or monkeypatched
  `subprocess.run`/`Popen` (transcribe) — except the git tests, which do use
  the real `git` binary against a disposable temp repo, since it's assumed
  available wherever mimama runs.
- No database, no auth (trusted-network tool), no JS build step (tailwind +
  daisyui via CDN), no build-your-own job queue — the one exception is an
  in-memory, single-process `TranscriptionJobRegistry` (a plain dict) backing
  the SSE console-output stream; keep this simplicity bar unless scale demands
  otherwise.

## Deferred / future options

- Slack listener (protocol is ready).
- Embeddings/vector index for the query prefilter, only if keyword filtering
  proves too weak on large trees; the litellm endpoint can serve embeddings
  without architectural change.
- Swapping `git_ops.py`'s shell-out for a Python git library (e.g. `pygit2`),
  if the shell dependency ever becomes a real constraint.
