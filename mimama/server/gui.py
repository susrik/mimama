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
