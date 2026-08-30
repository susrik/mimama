# mimama setup

## 1. App setup

### Local (development)

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
cp mimama.example.json mimama.json     # edit: notes_root, llm block, etc.
```

Run the server (uvicorn is deliberately not a project dependency — install it
ad hoc):

```sh
pip install uvicorn
uvicorn mimama.server.app:create_app --factory
```

The server finds its config via the `MIMAMA_CONFIG_FILENAME` environment
variable (default: `mimama.json` in the working directory).

Run the checks:

```sh
pytest            # tests + coverage (fails under 95%)
mypy mimama
ruff check .
```

### CLI

```sh
mimama --config mimama.json ls
mimama --config mimama.json convert     # guided: prompts for anything missing
mimama --config mimama.json query "notes mentioning EuroHPC in the last 2 days"
```

`--config` defaults to `mimama.json`. The CLI talks HTTP to the running
server (`server.url` in the config).

### Docker

```sh
cp mimama.example.json mimama.json      # edit; keep notes_root=/data/notes
docker compose up
```

`docker-compose.yml` mounts `./mimama.json` read-only into the container and
persists notes in a named volume mounted at `/data/notes`.

## 2. Configuration reference

See `mimama.example.json`. Highlights:

| key | meaning |
|---|---|
| `notes_root` | directory containing the notes tree |
| `llm.base_url`, `llm.api_key`, `llm.model` | any OpenAI-protocol endpoint (e.g. litellm) |
| `server.host`, `server.port` | bind address for uvicorn/docker |
| `server.url` | base URL clients (CLI, listeners, MCP) use to reach the server |
| `user.name`, `user.aliases` | lets queries like "notes mentioning me" resolve |
| `listeners.discord.enabled`, `.token` | start the Discord bot with the server |
| `mcp.http_enabled` | mount the MCP streamable-HTTP endpoint at `/mcp` |
| `query.max_candidates`, `query.excerpt_chars` | token-cost bounds for smart queries |

## 3. MCP access

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
