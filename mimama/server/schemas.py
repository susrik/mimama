"""Pydantic models for every /api request and response body.

This is the single typed contract shared by the server, CLI, listeners, and
MCP tools — frontends import these models instead of handling raw dicts.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    detail: str


# -- tree / notes -----------------------------------------------------------


class TreeEntryModel(BaseModel):
    path: str
    name: str
    is_dir: bool
    has_context: bool = False


class TreeResponse(BaseModel):
    path: str
    entries: list[TreeEntryModel]


class FoldersResponse(BaseModel):
    folders: list[str]


class NoteContent(BaseModel):
    path: str
    content: str


class NoteWriteRequest(BaseModel):
    path: str
    content: str


class NoteWriteResponse(BaseModel):
    path: str


class NoteDeleteRequest(BaseModel):
    path: str


class NoteDeleteResponse(BaseModel):
    path: str


class NoteRenameRequest(BaseModel):
    old_path: str
    new_path: str


class NoteRenameResponse(BaseModel):
    old_path: str
    new_path: str


class NoteCreateRequest(BaseModel):
    path: str
    content: str | None = None


class NoteCreateResponse(BaseModel):
    path: str


class TranscriptionRequest(BaseModel):
    audio_file: str
    model_path: str | None = None


class TranscriptionResponse(BaseModel):
    transcript: str


class ContextResponse(BaseModel):
    path: str
    context: str


# -- search -------------------------------------------------------------------


class SearchRequest(BaseModel):
    terms: list[str]
    subtree: str = ""
    any_of: bool = True


class SearchHitModel(BaseModel):
    path: str
    line_number: int
    line: str


class SearchResponse(BaseModel):
    hits: list[SearchHitModel]


# -- LLM operations -----------------------------------------------------------


class ConvertRequest(BaseModel):
    folder: str
    transcript: str
    filename: str | None = None
    instructions: str | None = None
    context: str | None = None


class CleanupRequest(BaseModel):
    path: str


class OperationResponse(BaseModel):
    path: str
    content: str


class InferenceRequest(BaseModel):
    path: str
    prompt: str


class InferenceResponse(BaseModel):
    content: str


# -- natural-language query -----------------------------------------------------


class QueryRequest(BaseModel):
    question: str


class QueryPlanModel(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    subtree: str | None = None
    semantic_question: str | None = None


class QueryResultModel(BaseModel):
    path: str
    note_date: date
    snippet: str
    reason: str = ""


class QueryResponse(BaseModel):
    plan: QueryPlanModel
    results: list[QueryResultModel]
