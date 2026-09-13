# mimama setup

This doc covers three audiences: users setting up a running instance,
developers setting up a dev environment, and developers extending the code.

## 1. Users: running mimama

### Local

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e .
cp mimama.example.json mimama.json     # edit: notes.root, llm block, etc.
pip install uvicorn                    # server runner (not a project dep)
uvicorn mimama.server.app:create_app --factory
```

The server finds its config via the `MIMAMA_CONFIG_FILENAME` environment
variable (default: `mimama.json` in the working directory).

### Docker

```sh
cp mimama.example.json mimama.json      # edit; keep notes.root=/data/notes
docker compose up
```

`docker-compose.yml` mounts `./mimama.json` read-only into the container and
persists notes in a named volume mounted at `/data/notes`.

### Enabling git staging

Set `"notes": {"git": true}` in the config. `notes.root` must already be a
git repository (mimama never runs `git init`) — mimama only ever *stages*
changes (`git add`) after every note write/delete/rename/convert/cleanup, from
both `/api` and the web UI; it never commits, so you stay in control of
history. `notes.git_exe` overrides the `git` executable if it's not on PATH.

### Setting up audio transcription

The "New file" → AI → meeting transcript → "Transcribe…" flow (and the
`/api/transcribe` endpoint used by the CLI) shells out to two external
binaries — neither is a Python dependency, so install them separately:

- [`ffmpeg`](https://ffmpeg.org/) — used to convert `.m4a` uploads to 16kHz
  mono `.wav` before transcription. Configurable via `transcripts.ffmpeg`
  (default: `ffmpeg`, found on PATH).
- [`whisper-cli`](https://github.com/ggml-org/whisper.cpp) (from whisper.cpp)
  — runs the actual speech-to-text. Configurable via
  `transcripts.whisper.cli` (default: `whisper-cli`, found on PATH).
- `transcripts.whisper.model` (required) — path to a whisper.cpp `ggml` model
  file, e.g. `~/whisper/models/ggml-large-v3-turbo.bin`. Download models from
  the [whisper.cpp models repo](https://github.com/ggml-org/whisper.cpp/tree/master/models).

## 2. Developers: dev environment setup

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
cp mimama.example.json mimama.json     # edit: notes.root, llm block, etc.
pip install uvicorn                    # server runner (not a project dep)
uvicorn mimama.server.app:create_app --factory
```

Run the checks:

```sh
pytest            # tests + coverage (fails under 95%)
mypy mimama
ruff check .
```

Tests never hit a real LLM, git binary logic uses a scratch `git init`-ed
temp repo, and `ffmpeg`/`whisper-cli` subprocess calls are monkeypatched —
see `AGENTS.md` for the full testing conventions.

### CLI

```sh
mimama --config mimama.json ls
mimama --config mimama.json convert     # guided: prompts for anything missing
mimama --config mimama.json query "notes mentioning EuroHPC in the last 2 days"
```

`--config` defaults to `mimama.json`. The CLI talks HTTP to the running
server (`server.url` in the config).

## 3. Developers: extending the code

See `AGENTS.md` for the package map and architectural conventions (kept there
as the single source of truth, so it doesn't drift from this doc). Quick
pointers for common changes:

- **New LLM-backed operation** (like convert/cleanup): add the logic to
  `mimama/core/operations.py`, a request/response pair to
  `mimama/server/schemas.py`, an endpoint in `mimama/server/api.py`, and (if
  it should be reachable from the web UI) a `/gui` route + template.
- **New listener platform** (e.g. Slack): add a config sub-model next to
  `DiscordConfig` in `mimama/config.py`, and a new module in
  `mimama/listeners/` implementing the `Listener` protocol from `base.py`,
  reusing `router.py` for command parsing/dispatch.
- **Git / transcription internals**: both are isolated behind small,
  swappable interfaces on purpose — `mimama/core/git_ops.py` (shell `git`
  today; a future swap to a Python git library only touches this module) and
  `mimama/core/transcribe.py` (`run_transcription_pipeline` for the
  synchronous path-based `/api/transcribe`; `TranscriptionJob` /
  `TranscriptionJobRegistry` for the background-thread, upload-based `/gui`
  flow that streams console output over SSE).

## 4. Configuration reference

See `mimama.example.json`. Highlights:

| key | meaning |
|---|---|
| `notes.root` | directory containing the notes tree |
| `notes.git` | stage every note mutation with git (default `false`) |
| `notes.git_exe` | path to the `git` executable (default `git`) |
| `llm.base_url`, `llm.api_key`, `llm.model` | any OpenAI-protocol endpoint (e.g. litellm) |
| `transcripts.ffmpeg` | path to `ffmpeg` (default `ffmpeg`) |
| `transcripts.whisper.cli` | path to `whisper-cli` (default `whisper-cli`) |
| `transcripts.whisper.model` | path to a whisper.cpp `ggml` model file (required) |
| `server.host`, `server.port` | bind address for uvicorn/docker |
| `server.url` | base URL clients (CLI, listeners, MCP) use to reach the server |
| `user.name`, `user.aliases` | lets queries like "notes mentioning me" resolve |
| `listeners.discord.enabled`, `.token` | start the Discord bot with the server |
| `mcp.http_enabled` | mount the MCP streamable-HTTP endpoint at `/mcp` |
| `query.max_candidates`, `query.excerpt_chars` | token-cost bounds for smart queries |

## 5. MCP access

The MCP tools (list/read/add/search notes, natural-language `query_notes`,
convert transcript, cleanup note, get context) are defined once and available
over two transports. The FastAPI server must be running in both cases.

### Local (stdio)

MCP clients spawn `mimama-mcp` themselves. opencode (`opencode.json`):

```json
{
  "mcp": {
    "mimama": {
      "type": "local",
      "command": ["mimama-mcp", "--config", "/absolute/path/to/mimama.json"]
    }
  }
}
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "mimama": {
      "command": "mimama-mcp",
      "args": ["--config", "/absolute/path/to/mimama.json"]
    }
  }
}
```

Note: `mimama-mcp` must be on PATH (i.e. the venv's bin dir, or use the
absolute path to the script).

### Remote (streamable HTTP)

Set `"mcp": {"http_enabled": true}` in the config; the server then exposes
MCP at `http://<host>:8000/mcp`. This is the natural transport when running
in Docker. opencode:

```json
{
  "mcp": {
    "mimama": {
      "type": "remote",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Caveat: mimama has no authentication — it is a trusted-network/localhost
tool. Do not expose the port publicly.
