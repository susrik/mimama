# mimama

LLM-assisted manager for a human-readable tree of markdown meeting notes.

- Notes live in a plain directory tree — the filesystem is the data model.
- Each folder can carry an `AGENTS.md` giving LLM context; context is
  assembled hierarchically (notes root → target folder) for every operation.
- Core operations: convert raw transcripts to meeting minutes, clean up rough
  notes, full-text search, and LLM-powered natural-language queries
  ("all notes mentioning EuroHPC in the last 2 days").
- The web UI's "New file" flow can generate a note with AI, optionally from a
  meeting transcript — including transcribing an uploaded `.wav`/`.m4a` file
  (via `ffmpeg` + `whisper-cli`) with real-time console output.
- Optional git integration (`notes.git`): every note write/delete/rename is
  staged (never committed) automatically.
- One FastAPI server; web UI (htmx), CLI (click), Discord bot, and MCP server
  are all thin HTTP clients of its `/api`.

See [docs/setup.md](docs/setup.md) for installation, configuration (including
git and audio-transcription setup), and MCP client setup (local stdio and
remote HTTP). See [AGENTS.md](AGENTS.md) for architecture notes aimed at
maintainers and coding agents.

## Quick start

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
cp mimama.example.json mimama.json   # then edit
pip install uvicorn                  # server runner (not a project dep)
uvicorn mimama.server.app:create_app --factory
```

Or with Docker:

```sh
cp mimama.example.json mimama.json   # then edit
docker compose up
```
