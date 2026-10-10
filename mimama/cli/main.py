"""mimama CLI — a guided httpx client of the server's /api.

Every parameter is a normal click option for scripting, but anything missing
is prompted for interactively: folders and notes are picked from numbered
lists fetched from the server, transcripts can be typed, read from a file, or
composed in $EDITOR, and LLM operations show a summary and ask for
confirmation before sending.
"""

from __future__ import annotations

import click

from mimama.api_client import APIClient, APIError
from mimama.config import Config, click_config_callback


@click.group()
@click.option(
    "--config",
    "config",
    default="mimama.json",
    callback=click_config_callback,
    help="Path to the mimama JSON config file.",
    show_default=True,
)
@click.pass_context
def cli(ctx: click.Context, config: Config) -> None:
    """Manage a tree of markdown meeting notes (talks to the mimama server)."""
    ctx.obj = APIClient(config.server.url)


def _api(ctx: click.Context) -> APIClient:
    api: APIClient = ctx.obj
    return api


def _fail(exc: APIError) -> None:
    raise click.ClickException(str(exc))


# -- guided pickers -----------------------------------------------------------


def pick_folder(api: APIClient) -> str:
    """Numbered folder picker fed from /api/folders."""
    folders = api.folders().folders
    click.echo("Folders:")
    for index, folder in enumerate(folders, start=1):
        click.echo(f"  {index}. /{folder}")
    choice = click.prompt(
        "Target folder", type=click.IntRange(1, len(folders)), default=1
    )
    return folders[choice - 1]


def pick_note(api: APIClient, subtree: str = "") -> str:
    """Numbered note picker fed from /api/search-free tree walking."""
    notes: list[str] = []

    def walk(path: str) -> None:
        for entry in api.tree(path).entries:
            if entry.is_dir:
                walk(entry.path)
            else:
                notes.append(entry.path)

    walk(subtree)
    if not notes:
        raise click.ClickException("no notes found")
    click.echo("Notes:")
    for index, note in enumerate(notes, start=1):
        click.echo(f"  {index}. {note}")
    choice = click.prompt("Note", type=click.IntRange(1, len(notes)), default=1)
    return notes[choice - 1]


def read_transcript(transcript_file: str | None) -> str:
    """Transcript from --transcript-file, or interactively (file path/$EDITOR)."""
    if transcript_file:
        with open(transcript_file, encoding="utf-8") as handle:
            return handle.read()
    source = click.prompt(
        "Transcript source",
        type=click.Choice(["file", "editor"]),
        default="editor",
    )
    if source == "file":
        path = click.prompt("Transcript file path", type=click.Path(exists=True))
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    text = click.edit("")
    if not text or not text.strip():
        raise click.ClickException("empty transcript")
    return text


# -- commands -------------------------------------------------------------------


@cli.command()
@click.argument("path", default="")
@click.pass_context
def ls(ctx: click.Context, path: str) -> None:
    """List a folder in the notes tree."""
    try:
        entries = _api(ctx).tree(path).entries
    except APIError as exc:
        _fail(exc)
    for entry in entries:
        marker = "d" if entry.is_dir else "-"
        click.echo(f"{marker} {entry.path}")


@cli.command()
@click.argument("path", required=False)
@click.pass_context
def show(ctx: click.Context, path: str | None) -> None:
    """Show a note (picked interactively when PATH is omitted)."""
    api = _api(ctx)
    try:
        if path is None:
            path = pick_note(api)
        click.echo(api.read_note(path).content)
    except APIError as exc:
        _fail(exc)


@cli.command()
@click.argument("terms", nargs=-1, required=True)
@click.option("--subtree", default="", help="Restrict the search to a folder.")
@click.pass_context
def search(ctx: click.Context, terms: tuple[str, ...], subtree: str) -> None:
    """Exact full-text search (case-insensitive, no LLM)."""
    try:
        hits = _api(ctx).search(list(terms), subtree=subtree).hits
    except APIError as exc:
        _fail(exc)
    if not hits:
        click.echo("(no matches)")
    for hit in hits:
        click.echo(f"{hit.path}:{hit.line_number}: {hit.line}")


@cli.command()
@click.argument("question", required=False)
@click.pass_context
def query(ctx: click.Context, question: str | None) -> None:
    """Ask a natural-language question about the notes (LLM-powered)."""
    if question is None:
        question = click.prompt("Question")
    try:
        response = _api(ctx).query(question)
    except APIError as exc:
        _fail(exc)
    if not response.results:
        click.echo("(no matching notes)")
        return
    for result in response.results:
        line = f"{result.path} ({result.note_date})"
        if result.reason:
            line += f" — {result.reason}"
        click.echo(line)
        click.echo(f"    {result.snippet}")


@cli.command()
@click.option("--folder", default=None, help="Target folder (picked if omitted).")
@click.option("--transcript-file", default=None, type=click.Path(exists=True),
              help="Read the transcript from this file (prompted if omitted).")
@click.option("--filename", default=None, help="Output filename (dated default).")
@click.option("--instructions", default=None, help="Override the transcript instructions.")
@click.option("--context", default=None, help="Additional context for the transcript.")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
def convert(
    ctx: click.Context,
    folder: str | None,
    transcript_file: str | None,
    filename: str | None,
    instructions: str | None,
    context: str | None,
    yes: bool,
) -> None:
    """Convert a raw transcript into meeting minutes (LLM-powered)."""
    api = _api(ctx)
    try:
        if folder is None:
            folder = pick_folder(api)
        transcript = read_transcript(transcript_file)
        click.echo(f"Folder:   /{folder}")
        click.echo(f"Filename: {filename or '(dated default)'}")
        click.echo(f"Transcript: {len(transcript)} characters")
        if not yes and not click.confirm("Send to LLM?", default=True):
            raise click.Abort()
        result = api.convert(
            folder, transcript, filename=filename, instructions=instructions, context=context
        )
    except APIError as exc:
        _fail(exc)
    click.echo(f"saved: {result.path}")
    click.echo(result.content)


@cli.command()
@click.argument("path", required=False)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
def cleanup(ctx: click.Context, path: str | None, yes: bool) -> None:
    """Clean up a rough note into polished minutes, in place (LLM-powered)."""
    api = _api(ctx)
    try:
        if path is None:
            path = pick_note(api)
        if not yes and not click.confirm(f"Clean up {path} with the LLM?",
                                         default=True):
            raise click.Abort()
        result = api.cleanup(path)
    except APIError as exc:
        _fail(exc)
    click.echo(f"saved: {result.path}")
    click.echo(result.content)


@cli.command()
@click.argument("path", default="")
@click.pass_context
def context(ctx: click.Context, path: str) -> None:
    """Show the assembled hierarchical AGENTS.md context for a folder."""
    try:
        response = _api(ctx).context(path)
    except APIError as exc:
        _fail(exc)
    click.echo(response.context or "(no context files on this path)")


@cli.command()
@click.option("--path", required=True, help="Path to the file to create")
@click.option("--content", default="", help="Initial content for the file")
@click.pass_context
def create(ctx: click.Context, path: str, content: str) -> None:
    """Create a new note file."""
    try:
        result = _api(ctx).create_note(path=path, content=content)
        click.echo(f"Created: {result.path}")
    except APIError as exc:
        _fail(exc)


@cli.command()
@click.option("--old-path", required=True, help="Current path of the file")
@click.option("--new-path", required=True, help="New path for the file")
@click.pass_context
def rename(ctx: click.Context, old_path: str, new_path: str) -> None:
    """Rename a note file."""
    try:
        result = _api(ctx).rename_note(old_path=old_path, new_path=new_path)
        click.echo(f"Renamed: {result.old_path} → {result.new_path}")
    except APIError as exc:
        _fail(exc)


@cli.command()
@click.option("--path", required=True, help="Path to the file to delete")
@click.pass_context
def delete(ctx: click.Context, path: str) -> None:
    """Delete a note file."""
    try:
        result = _api(ctx).delete_note(path=path)
        click.echo(f"Deleted: {result.path}")
    except APIError as exc:
        _fail(exc)


@cli.command()
@click.option("--path", required=True, help="Path to the file for inference")
@click.option("--prompt", required=True, help="Prompt for the LLM")
@click.pass_context
def inference(ctx: click.Context, path: str, prompt: str) -> None:
    """Run AI inference on a note with context."""
    try:
        result = _api(ctx).run_inference(path=path, prompt=prompt)
        click.echo(result.content)
    except APIError as exc:
        _fail(exc)


@cli.command()
@click.option("--audio-file", required=True, help="Path to audio file")
@click.option("--model-path", default=None, help="Path to whisper model file")
@click.pass_context
def transcribe(ctx: click.Context, audio_file: str, model_path: str | None) -> None:
    """Transcribe an audio file to text."""
    try:
        response = _api(ctx).transcribe(
            audio_file=audio_file,
            model_path=model_path
        )
        click.echo(response.transcript)
    except APIError as exc:
        _fail(exc)
