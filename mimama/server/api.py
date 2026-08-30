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
from mimama.core.llm import LLMError, SupportsComplete
from mimama.core.notes import NoteError, NoteStore
from mimama.core.operations import cleanup_note, transcript_to_minutes
from mimama.core.query import QueryError, run_query
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
def write_note(store: Store, body: schemas.NoteWriteRequest) -> schemas.NoteWriteResponse:
    try:
        written = store.write(body.path, body.content)
    except NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return schemas.NoteWriteResponse(path=written)


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
    store: Store, llm: LLM, body: schemas.ConvertRequest
) -> schemas.OperationResponse:
    try:
        result = transcript_to_minutes(
            store, llm, body.folder, body.transcript, filename=body.filename
        )
    except NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return schemas.OperationResponse(path=result.path, content=result.content)


@router.post("/operations/cleanup", response_model=schemas.OperationResponse)
def cleanup(
    store: Store, llm: LLM, body: schemas.CleanupRequest
) -> schemas.OperationResponse:
    try:
        result = cleanup_note(store, llm, body.path)
    except NoteError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return schemas.OperationResponse(path=result.path, content=result.content)


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
