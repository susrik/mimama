"""/gui (+ /) — GUI-internal htmx surface. HTML in, HTML out.

These endpoints serve pages and fragments for the web UI and are free to
change with it; external clients must use /api instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from mimama.config import Config
from mimama.core.context import assemble_context
from mimama.core.llm import LLMError, SupportsComplete
from mimama.core.notes import NoteError, NoteStore
from mimama.core.operations import cleanup_note, transcript_to_minutes
from mimama.core.query import QueryError, run_query
from mimama.core.transcribe import run_transcription_pipeline

router = APIRouter()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _store(request: Request) -> NoteStore:
    store: NoteStore = request.app.state.store
    return store


def _llm(request: Request) -> SupportsComplete:
    llm: SupportsComplete = request.app.state.llm
    return llm


def _config(request: Request) -> Config:
    config: Config = request.app.state.config
    return config


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@router.get("/gui/tree", response_class=HTMLResponse)
def tree(request: Request, path: str = "") -> HTMLResponse:
    store = _store(request)
    try:
        entries = store.list_dir(path)
    except NoteError as exc:
        return _error(request, str(exc))
    parent = "/".join(path.split("/")[:-1]) if path else None
    return templates.TemplateResponse(
        request,
        "tree.html",
        {"path": path, "parent": parent, "entries": entries},
    )


@router.get("/gui/note/create", response_class=HTMLResponse)
def create_note_form(request: Request, path: str = "") -> HTMLResponse:
    return templates.TemplateResponse(request, "create_note.html", {"path": path})


@router.post("/gui/note/create", response_class=HTMLResponse)
def create_note_submit(
    request: Request,
    path: Annotated[str, Form()],
    content: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        store = _store(request)
        full_path = (path + "/" + "new-note.md").lstrip("/")
        result = store.write(full_path, content or "")
    except NoteError as exc:
        return _error(request, str(exc))

    # Redirect to the new note
    return templates.TemplateResponse(
        request, "note.html", {"path": full_path, "content": content or "", "context": ""}
    )


@router.get("/gui/note/rename", response_class=HTMLResponse)
def rename_note_form(request: Request, old_path: str, new_path: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "rename_note.html",
        {"old_path": old_path, "new_path": new_path or old_path}
    )


@router.post("/gui/note/rename", response_class=HTMLResponse)
def rename_note_submit(
    request: Request,
    old_path: Annotated[str, Form()],
    new_path: Annotated[str, Form()],
) -> HTMLResponse:
    try:
        store = _store(request)
        old_path_obj = store.resolve(old_path)
        if not old_path_obj.is_file():
            raise NoteError(f"no such note: {old_path!r}")

        new_path_obj = store.resolve(new_path)
        # Create parent directories for new path
        new_path_obj.parent.mkdir(parents=True, exist_ok=True)
        old_path_obj.rename(new_path_obj)
    except NoteError as exc:
        return _error(request, str(exc))

    # Redirect to the renamed note
    return templates.TemplateResponse(
        request, "note.html", {"path": new_path, "content": store.read(new_path), "context": ""}
    )


@router.post("/gui/note/delete", response_class=HTMLResponse)
def delete_note_submit(request: Request, path: Annotated[str, Form()]) -> HTMLResponse:
    try:
        store = _store(request)
        path_obj = store.resolve(path)
        if not path_obj.is_file():
            raise NoteError(f"no such note: {path!r}")
        path_obj.unlink()
    except NoteError as exc:
        return _error(request, str(exc))

    # Redirect to parent directory
    parent = "/".join(path.split("/")[:-1]) if path else ""
    return templates.TemplateResponse(
        request, "tree.html", {"path": parent, "parent": None, "entries": store.list_dir(parent)}
    )


@router.get("/gui/note", response_class=HTMLResponse)
def note(request: Request, path: str) -> HTMLResponse:
    store = _store(request)
    try:
        content = store.read(path)
    except NoteError as exc:
        return _error(request, str(exc))
    context = assemble_context(store, path)
    return templates.TemplateResponse(
        request,
        "note.html",
        {"path": path, "content": content, "context": context},
    )


@router.get("/gui/convert", response_class=HTMLResponse)
def convert_form(request: Request) -> HTMLResponse:
    folders = _store(request).list_folders()
    return templates.TemplateResponse(request, "convert.html", {"folders": folders})


@router.post("/gui/transcribe", response_class=HTMLResponse)
def transcribe_submit(
    request: Request,
    audio_file: Annotated[str, Form()],
    model_path: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    try:
        # For now, we'll just handle the file upload in a basic way
        # In a real implementation, you'd want to handle file uploads properly
        result = run_transcription_pipeline(
            _config(request),
            audio_file,
            model_path,
        )
    except (NoteError, LLMError) as exc:
        return _error(request, str(exc))

    return templates.TemplateResponse(
        request, "transcription_result.html", {"transcript": result}
    )


@router.get("/gui/transcribe-form", response_class=HTMLResponse)
def transcribe_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "transcribe.html")


@router.post("/gui/convert", response_class=HTMLResponse)
def convert_submit(
    request: Request,
    folder: Annotated[str, Form()],
    transcript: Annotated[str, Form()],
    filename: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        result = transcript_to_minutes(
            _store(request),
            _llm(request),
            folder,
            transcript,
            filename=filename or None,
        )
    except (NoteError, LLMError) as exc:
        return _error(request, str(exc))
    return templates.TemplateResponse(
        request, "result.html", {"path": result.path, "content": result.content}
    )


@router.post("/gui/cleanup", response_class=HTMLResponse)
def cleanup_submit(request: Request, path: Annotated[str, Form()]) -> HTMLResponse:
    try:
        result = cleanup_note(_store(request), _llm(request), path)
    except (NoteError, LLMError) as exc:
        return _error(request, str(exc))
    return templates.TemplateResponse(
        request, "result.html", {"path": result.path, "content": result.content}
    )


@router.get("/gui/note/inference", response_class=HTMLResponse)
def inference_form(request: Request, path: str) -> HTMLResponse:
    context = assemble_context(_store(request), path)
    return templates.TemplateResponse(
        request, "inference.html", {"path": path, "context": context}
    )


@router.post("/gui/note/inference", response_class=HTMLResponse)
def inference_submit(
    request: Request,
    path: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
) -> HTMLResponse:
    try:
        result = _llm(request).complete(
            system=assemble_context(_store(request), path) or "You are an assistant that manages a tree of markdown meeting notes.",
            user=prompt,
        )
    except (NoteError, LLMError) as exc:
        return _error(request, str(exc))

    return templates.TemplateResponse(
        request, "inference_result.html", {"path": path, "prompt": prompt, "content": result}
    )


@router.post("/gui/query", response_class=HTMLResponse)
def query_submit(request: Request, question: Annotated[str, Form()]) -> HTMLResponse:
    config = _config(request)
    try:
        _, results = run_query(
            _store(request), _llm(request), question, config.user, config.query
        )
    except (QueryError, LLMError) as exc:
        return _error(request, str(exc))
    return templates.TemplateResponse(
        request, "query_results.html", {"question": question, "results": results}
    )


def _error(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "error.html", {"message": message}, status_code=200
    )
