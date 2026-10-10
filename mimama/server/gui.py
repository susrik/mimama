"""/gui (+ /) — GUI-internal htmx surface. HTML in, HTML out (except the small
JSON job-id response from the transcription/inference upload/start endpoints,
consumed by a few lines of inline JS, and the SSE console streams).

These endpoints serve pages and fragments for the web UI and are free to
change with it; external clients must use /api instead.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from mimama.config import Config
from mimama.core.context import AttachedFile, assemble_context, list_context_files
from mimama.core.git_ops import git_enabled, stage_file
from mimama.core.inference_jobs import DONE as JOB_DONE
from mimama.core.inference_jobs import InferenceJob, InferenceJobRegistry
from mimama.core.llm import LLMError, SupportsComplete
from mimama.core.naming import default_note_filename
from mimama.core.notes import NoteError, NoteStore
from mimama.core.operations import (
    DEFAULT_TRANSCRIPT_INSTRUCTIONS,
    cleanup_note,
    generate_note_content,
    transcript_to_minutes,
)
from mimama.core.query import run_query
from mimama.core.transcribe import DONE, TranscriptionJobRegistry

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


def _jobs(request: Request) -> TranscriptionJobRegistry:
    registry: TranscriptionJobRegistry = request.app.state.transcription_jobs
    return registry


def _inference_jobs(request: Request) -> InferenceJobRegistry:
    registry: InferenceJobRegistry = request.app.state.inference_jobs
    return registry


def _render(name: str, context: dict[str, object]) -> str:
    """Render a template fragment to a string (for embedding in a JSON SSE payload)."""
    return templates.get_template(name).render(context)


def _maybe_stage(config: Config, *paths: str) -> None:
    """Stage the given note paths if git integration is enabled."""
    if git_enabled(config):
        for path in paths:
            stage_file(config, path)


def _attached_from_uploads(files: list[UploadFile] | None) -> list[AttachedFile] | None:
    """Convert multipart-uploaded files into attached-file context."""
    if not files:
        return None
    attached = [
        AttachedFile(
            name=f.filename or "upload",
            content=f.file.read().decode("utf-8", errors="replace"),
        )
        for f in files
    ]
    return attached or None


def _job_events(registry: InferenceJobRegistry, job_id: str, unknown_message: str) -> Iterator[str]:
    """Shared SSE loop for all inference-job stream endpoints."""
    job = registry.get(job_id)
    if job is None:
        yield f"event: done\ndata: {json.dumps({'error': unknown_message})}\n\n"
        return
    while True:
        chunk = job.lines.get()
        if chunk is JOB_DONE:
            payload = {"html": job.html, "error": job.error, "cancelled": job.cancelled}
            yield f"event: done\ndata: {json.dumps(payload)}\n\n"
            registry.discard(job_id)
            return
        yield f"data: {json.dumps(str(chunk))}\n\n"


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
    store = _store(request)
    context_files = list_context_files(store, path) if path else list_context_files(store, "")
    return templates.TemplateResponse(
        request,
        "new_note.html",
        {
            "folder": path,
            "default_filename": default_note_filename(),
            "context_files": context_files,
            "default_transcript_instructions": DEFAULT_TRANSCRIPT_INSTRUCTIONS,
        },
    )


@router.post("/gui/note/create", response_class=HTMLResponse)
def create_note_submit(
    request: Request,
    folder: Annotated[str, Form()] = "",
    filename: Annotated[str, Form()] = "",
    content: Annotated[str, Form()] = "",
    ai: Annotated[str | None, Form()] = None,
    context: Annotated[str, Form()] = "",
    transcript: Annotated[str | None, Form()] = None,
    transcript_instructions: Annotated[str, Form()] = "",
    context_files: Annotated[list[UploadFile] | None, File()] = None,
) -> HTMLResponse:
    store = _store(request)
    config = _config(request)
    filename = filename or default_note_filename()
    target = (PurePosixPath(folder) / filename).as_posix()

    try:
        if ai and transcript:
            result = transcript_to_minutes(
                store,
                _llm(request),
                folder,
                content,
                filename=filename,
                instructions=transcript_instructions or None,
                context=context or None,
                files=_attached_from_uploads(context_files),
            )
            path, written_content = result.path, result.content
        else:
            written_content = content
            path = store.write(target, written_content)
    except (NoteError, LLMError) as exc:
        return _error(request, str(exc))

    _maybe_stage(config, path)
    return templates.TemplateResponse(
        request, "result.html", {"path": path, "content": written_content}
    )


@router.post("/gui/note/create/generate/start")
def generate_start(
    request: Request,
    folder: Annotated[str, Form()] = "",
    filename: Annotated[str, Form()] = "",
    content: Annotated[str, Form()] = "",
    context: Annotated[str, Form()] = "",
    context_files: Annotated[list[UploadFile] | None, File()] = None,
) -> dict[str, str]:
    """Start a background, streamed free-form AI generation for the "new
    file" flow. On success the job writes the note itself and its ``html``
    is a rendered ``result.html`` fragment, ready to swap into ``#main``."""
    store = _store(request)
    config = _config(request)
    llm = _llm(request)
    resolved_filename = filename or default_note_filename()
    target = (PurePosixPath(folder) / resolved_filename).as_posix()
    files = _attached_from_uploads(context_files)

    def work(job: InferenceJob) -> None:
        written_content = generate_note_content(
            store,
            llm,
            target,
            context,
            content,
            on_chunk=job.emit,
            cancel_event=job.cancel_event,
            files=files,
        )
        path = store.write(target, written_content)
        _maybe_stage(config, path)
        job.html = _render("result.html", {"path": path, "content": written_content})

    job = _inference_jobs(request).create(work)
    return {"job_id": job.id}


@router.get("/gui/note/create/generate/stream/{job_id}")
def generate_stream(request: Request, job_id: str) -> StreamingResponse:
    registry = _inference_jobs(request)
    return StreamingResponse(
        _job_events(registry, job_id, "unknown generation job"),
        media_type="text/event-stream",
    )


@router.post("/gui/note/create/generate/cancel/{job_id}")
def generate_cancel(request: Request, job_id: str) -> dict[str, bool]:
    _inference_jobs(request).cancel(job_id)
    return {"ok": True}


@router.post("/gui/transcribe/start")
async def transcribe_start(
    request: Request, audio_file: UploadFile
) -> dict[str, str]:
    config = _config(request)
    audio_bytes = await audio_file.read()
    job = _jobs(request).create(config, audio_file.filename or "upload", audio_bytes)
    return {"job_id": job.id}


@router.get("/gui/transcribe/stream/{job_id}")
def transcribe_stream(request: Request, job_id: str) -> StreamingResponse:
    registry = _jobs(request)
    job = registry.get(job_id)

    def _events() -> Iterator[str]:
        if job is None:
            yield f"event: done\ndata: {json.dumps({'error': 'unknown transcription job'})}\n\n"
            return
        while True:
            line = job.lines.get()
            if line is DONE:
                payload = {"transcript": job.transcript, "error": job.error}
                yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                registry.discard(job_id)
                return
            yield f"data: {json.dumps(str(line))}\n\n"

    return StreamingResponse(_events(), media_type="text/event-stream")


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

    _maybe_stage(_config(request), old_path, new_path)
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

    _maybe_stage(_config(request), path)
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


@router.post("/gui/cleanup", response_class=HTMLResponse)
def cleanup_submit(request: Request, path: Annotated[str, Form()]) -> HTMLResponse:
    try:
        result = cleanup_note(_store(request), _llm(request), path)
    except (NoteError, LLMError) as exc:
        return _error(request, str(exc))
    _maybe_stage(_config(request), result.path)
    return templates.TemplateResponse(
        request, "result.html", {"path": result.path, "content": result.content}
    )


@router.get("/gui/note/inference", response_class=HTMLResponse)
def inference_form(request: Request, path: str) -> HTMLResponse:
    context = assemble_context(_store(request), path)
    return templates.TemplateResponse(
        request, "inference.html", {"path": path, "context": context}
    )


@router.post("/gui/note/inference/start")
def inference_start(
    request: Request,
    path: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
) -> dict[str, str]:
    store = _store(request)
    llm = _llm(request)

    def work(job: InferenceJob) -> None:
        system = assemble_context(store, path) or (
            "You are an assistant that manages a tree of markdown meeting notes."
        )
        content = llm.stream_complete(
            system=system, user=prompt, on_chunk=job.emit, cancel_event=job.cancel_event
        )
        job.html = _render(
            "inference_result.html", {"path": path, "prompt": prompt, "content": content}
        )

    job = _inference_jobs(request).create(work)
    return {"job_id": job.id}


@router.get("/gui/note/inference/stream/{job_id}")
def inference_stream(request: Request, job_id: str) -> StreamingResponse:
    registry = _inference_jobs(request)
    return StreamingResponse(
        _job_events(registry, job_id, "unknown inference job"),
        media_type="text/event-stream",
    )


@router.post("/gui/note/inference/cancel/{job_id}")
def inference_cancel(request: Request, job_id: str) -> dict[str, bool]:
    _inference_jobs(request).cancel(job_id)
    return {"ok": True}


@router.post("/gui/query/start")
def query_start(request: Request, question: Annotated[str, Form()]) -> dict[str, str]:
    store = _store(request)
    llm = _llm(request)
    config = _config(request)

    def work(job: InferenceJob) -> None:
        _, results = run_query(
            store,
            llm,
            question,
            config.user,
            config.query,
            on_chunk=job.emit,
            cancel_event=job.cancel_event,
        )
        job.html = _render("query_results.html", {"question": question, "results": results})

    job = _inference_jobs(request).create(work)
    return {"job_id": job.id}


@router.get("/gui/query/stream/{job_id}")
def query_stream(request: Request, job_id: str) -> StreamingResponse:
    registry = _inference_jobs(request)
    return StreamingResponse(
        _job_events(registry, job_id, "unknown query job"),
        media_type="text/event-stream",
    )


@router.post("/gui/query/cancel/{job_id}")
def query_cancel(request: Request, job_id: str) -> dict[str, bool]:
    _inference_jobs(request).cancel(job_id)
    return {"ok": True}


def _error(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "error.html", {"message": message}, status_code=200
    )
