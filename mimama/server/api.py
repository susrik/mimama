"""/api — the user-facing JSON API (consumed by CLI, listeners, MCP, GUI-external).

All request/response bodies are pydantic models from
:mod:`mimama.server.schemas`. Errors raise HTTPException with a plain
``detail`` string (matching :class:`schemas.ErrorResponse`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from mimama.config import Config
from mimama.core.context import assemble_context
from mimama.core.git_ops import git_enabled, stage_file
from mimama.core.llm import LLMError, SupportsComplete
from mimama.core.notes import NoteError, NoteStore
from mimama.core.operations import cleanup_note, transcript_to_minutes
from mimama.core.query import QueryError, run_query
from mimama.core.transcribe import run_transcription_pipeline
from mimama.server import schemas

router = APIRouter(prefix="/api")


def get_store(request: Request) -> NoteStore:
    store: NoteStore = request.app.state.store
    return store


def get_llm(request: Request) -> SupportsComplete:
    llm: SupportsComplete = request.app.state.llm
    return llm


def get_config(request: Request) -> Config:
    config: Config = request.app.state.config
    return config


Store = Annotated[NoteStore, Depends(get_store)]
LLM = Annotated[SupportsComplete, Depends(get_llm)]
Conf = Annotated[Config, Depends(get_config)]


@router.get("/tree", response_model=schemas.TreeResponse)
def tree(store: Store, path: str = "") -> schemas.TreeResponse:
    try:
        entries = store.list_dir(path)
    except NoteError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return schemas.TreeResponse(
        path=path,
        entries=[
            schemas.TreeEntryModel(
                path=e.path, name=e.name, is_dir=e.is_dir, has_context=e.has_context
            )
            for e in entries
        ],
    )


@router.get("/folders", response_model=schemas.FoldersResponse)
def folders(store: Store) -> schemas.FoldersResponse:
    return schemas.FoldersResponse(folders=store.list_folders())


@router.get("/note", response_model=schemas.NoteContent)
def read_note(store: Store, path: str) -> schemas.NoteContent:
    try:
        content = store.read(path)
    except NoteError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return schemas.NoteContent(path=path, content=content)


@router.put("/note", response_model=schemas.NoteWriteResponse)
def write_note(
    store: Store, config: Conf, body: schemas.NoteWriteRequest
) -> schemas.NoteWriteResponse:
    try:
        written = store.write(body.path, body.content)
    except NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # If git is enabled, stage the file
    if git_enabled(config):
        stage_file(config, body.path)
    return schemas.NoteWriteResponse(path=written)


@router.delete("/note", response_model=schemas.NoteDeleteResponse)
def delete_note(
    store: Store, config: Conf, body: schemas.NoteDeleteRequest
) -> schemas.NoteDeleteResponse:
    try:
        path = store.resolve(body.path)
        if not path.is_file():
            raise NoteError(f"no such note: {body.path!r}")
        path.unlink()
    except NoteError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # If git is enabled, stage the deletion
    if git_enabled(config):
        stage_file(config, body.path)
    return schemas.NoteDeleteResponse(path=body.path)


@router.post("/note/rename", response_model=schemas.NoteRenameResponse)
def rename_note(
    store: Store, config: Conf, body: schemas.NoteRenameRequest
) -> schemas.NoteRenameResponse:
    try:
        old_path = store.resolve(body.old_path)
        if not old_path.is_file():
            raise NoteError(f"no such note: {body.old_path!r}")
        new_path = store.resolve(body.new_path)
        # Create parent directories for new path
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)
    except NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # If git is enabled, stage both files
    if git_enabled(config):
        stage_file(config, body.old_path)
        stage_file(config, body.new_path)
    return schemas.NoteRenameResponse(old_path=body.old_path, new_path=body.new_path)


@router.post("/note/create", response_model=schemas.NoteCreateResponse)
def create_note(
    store: Store, config: Conf, body: schemas.NoteCreateRequest
) -> schemas.NoteCreateResponse:
    try:
        # Create the note with default content
        path = store.write(body.path, body.content or "")
    except NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # If git is enabled, stage the new file
    if git_enabled(config):
        stage_file(config, body.path)
    return schemas.NoteCreateResponse(path=path)


@router.post("/transcribe", response_model=schemas.TranscriptionResponse)
def transcribe(
    config: Conf,
    body: schemas.TranscriptionRequest
) -> schemas.TranscriptionResponse:
    try:
        # Run the transcription pipeline
        transcript = run_transcription_pipeline(
            config,
            body.audio_file,
            body.model_path
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return schemas.TranscriptionResponse(transcript=transcript)


@router.get("/context", response_model=schemas.ContextResponse)
def get_context(store: Store, path: str = "") -> schemas.ContextResponse:
    try:
        context = assemble_context(store, path)
    except NoteError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return schemas.ContextResponse(path=path, context=context)


@router.post("/search", response_model=schemas.SearchResponse)
def search(store: Store, body: schemas.SearchRequest) -> schemas.SearchResponse:
    try:
        hits = store.search(body.terms, subtree=body.subtree, any_of=body.any_of)
    except NoteError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return schemas.SearchResponse(
        hits=[
            schemas.SearchHitModel(path=h.path, line_number=h.line_number, line=h.line)
            for h in hits
        ]
    )


@router.post("/operations/convert", response_model=schemas.OperationResponse)
def convert(
    store: Store, llm: LLM, config: Conf, body: schemas.ConvertRequest
) -> schemas.OperationResponse:
    try:
        result = transcript_to_minutes(
            store,
            llm,
            body.folder,
            body.transcript,
            filename=body.filename,
            instructions=body.instructions,
            context=body.context,
        )
    except NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if git_enabled(config):
        stage_file(config, result.path)
    return schemas.OperationResponse(path=result.path, content=result.content)


@router.post("/operations/cleanup", response_model=schemas.OperationResponse)
def cleanup(
    store: Store, llm: LLM, config: Conf, body: schemas.CleanupRequest
) -> schemas.OperationResponse:
    try:
        result = cleanup_note(store, llm, body.path)
    except NoteError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if git_enabled(config):
        stage_file(config, result.path)
    return schemas.OperationResponse(path=result.path, content=result.content)


@router.post("/operations/inference", response_model=schemas.InferenceResponse)
def run_inference(
    store: Store,
    llm: LLM,
    body: schemas.InferenceRequest
) -> schemas.InferenceResponse:
    try:
        # Assemble context for the target path
        context = assemble_context(store, body.path)

        # Run inference with the context
        result = llm.complete(
            system=context or "You are an assistant that manages a tree of markdown meeting notes.",
            user=body.prompt,
        )
    except NoteError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return schemas.InferenceResponse(content=result)


@router.post("/query", response_model=schemas.QueryResponse)
def query(
    store: Store, llm: LLM, config: Conf, body: schemas.QueryRequest
) -> schemas.QueryResponse:
    try:
        plan, results = run_query(
            store, llm, body.question, config.user, config.query
        )
    except QueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return schemas.QueryResponse(
        plan=schemas.QueryPlanModel(**plan.model_dump()),
        results=[
            schemas.QueryResultModel(
                path=r.path, note_date=r.note_date, snippet=r.snippet, reason=r.reason
            )
            for r in results
        ],
    )
