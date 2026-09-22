"""Typed HTTP client for the mimama /api surface.

Shared by the CLI, the listener command router, and the MCP tools — all
requests/responses use the pydantic models from :mod:`mimama.server.schemas`.
"""

from __future__ import annotations

import httpx

from mimama.server import schemas


class APIError(Exception):
    """The mimama server returned an error."""


class APIClient:
    """Small typed client for the mimama /api surface."""

    def __init__(
        self,
        server_url: str,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """``transport`` lets tests route requests to an in-process ASGI app."""
        self._client = httpx.Client(
            base_url=server_url, timeout=timeout, transport=transport
        )

    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.is_error:
            try:
                detail = schemas.ErrorResponse.model_validate_json(
                    response.text
                ).detail
            except ValueError:
                detail = response.text
            raise APIError(detail)
        return response

    def tree(self, path: str = "") -> schemas.TreeResponse:
        r = self._check(self._client.get("/api/tree", params={"path": path}))
        return schemas.TreeResponse.model_validate_json(r.text)

    def folders(self) -> schemas.FoldersResponse:
        r = self._check(self._client.get("/api/folders"))
        return schemas.FoldersResponse.model_validate_json(r.text)

    def read_note(self, path: str) -> schemas.NoteContent:
        r = self._check(self._client.get("/api/note", params={"path": path}))
        return schemas.NoteContent.model_validate_json(r.text)

    def write_note(self, path: str, content: str) -> schemas.NoteWriteResponse:
        body = schemas.NoteWriteRequest(path=path, content=content)
        r = self._check(self._client.put("/api/note", json=body.model_dump()))
        return schemas.NoteWriteResponse.model_validate_json(r.text)

    def context(self, path: str = "") -> schemas.ContextResponse:
        r = self._check(self._client.get("/api/context", params={"path": path}))
        return schemas.ContextResponse.model_validate_json(r.text)

    def search(
        self, terms: list[str], subtree: str = "", any_of: bool = True
    ) -> schemas.SearchResponse:
        body = schemas.SearchRequest(terms=terms, subtree=subtree, any_of=any_of)
        r = self._check(self._client.post("/api/search", json=body.model_dump()))
        return schemas.SearchResponse.model_validate_json(r.text)

    def convert(
        self, folder: str, transcript: str, filename: str | None = None
    ) -> schemas.OperationResponse:
        body = schemas.ConvertRequest(
            folder=folder, transcript=transcript, filename=filename
        )
        r = self._check(
            self._client.post("/api/operations/convert", json=body.model_dump())
        )
        return schemas.OperationResponse.model_validate_json(r.text)

    def cleanup(self, path: str) -> schemas.OperationResponse:
        body = schemas.CleanupRequest(path=path)
        r = self._check(
            self._client.post("/api/operations/cleanup", json=body.model_dump())
        )
        return schemas.OperationResponse.model_validate_json(r.text)

    def query(self, question: str) -> schemas.QueryResponse:
        body = schemas.QueryRequest(question=question)
        r = self._check(self._client.post("/api/query", json=body.model_dump()))
        return schemas.QueryResponse.model_validate_json(r.text)

    def create_note(self, path: str, content: str | None = None) -> schemas.NoteCreateResponse:
        body = schemas.NoteCreateRequest(path=path, content=content)
        r = self._check(
            self._client.post("/api/note/create", json=body.model_dump())
        )
        return schemas.NoteCreateResponse.model_validate_json(r.text)

    def rename_note(self, old_path: str, new_path: str) -> schemas.NoteRenameResponse:
        body = schemas.NoteRenameRequest(old_path=old_path, new_path=new_path)
        r = self._check(
            self._client.post("/api/note/rename", json=body.model_dump())
        )
        return schemas.NoteRenameResponse.model_validate_json(r.text)

    def delete_note(self, path: str) -> schemas.NoteDeleteResponse:
        body = schemas.NoteDeleteRequest(path=path)
        r = self._check(
            self._client.delete("/api/note", json=body.model_dump())
        )
        return schemas.NoteDeleteResponse.model_validate_json(r.text)

    def run_inference(self, path: str, prompt: str) -> schemas.InferenceResponse:
        body = schemas.InferenceRequest(path=path, prompt=prompt)
        r = self._check(
            self._client.post("/api/operations/inference", json=body.model_dump())
        )
        return schemas.InferenceResponse.model_validate_json(r.text)

    def transcribe(self, audio_file: str, model_path: str | None = None) -> schemas.TranscriptionResponse:
        body = schemas.TranscriptionRequest(audio_file=audio_file, model_path=model_path)
        r = self._check(
            self._client.post("/api/transcribe", json=body.model_dump())
        )
        return schemas.TranscriptionResponse.model_validate_json(r.text)


