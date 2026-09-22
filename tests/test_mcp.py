"""Tests for the MCP tools and both transports."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp import types
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import UnexpectedToolError

from mimama.api_client import APIClient
from mimama.config import Config
from mimama.mcp.server import build_server, main
from mimama.mcp.tools import register_tools
from tests.fakes import FakeLLMClient
from tests.test_cli import _TestClientTransport

EXPECTED_TOOLS = {
    "list_notes",
    "read_note",
    "add_note",
    "search_notes",
    "query_notes",
    "convert_transcript",
    "cleanup_note",
    "get_context",
}


@pytest.fixture()
def mcp_server(client: TestClient) -> MCPServer:
    server = MCPServer(name="mimama-test")
    api = APIClient("http://testserver", transport=_TestClientTransport(client))
    register_tools(server, api)
    return server


async def call_tool(server: MCPServer, name: str, args: dict[str, Any]) -> str:
    try:
        result = await server.call_tool(name, args)
    except UnexpectedToolError as exc:
        raise AssertionError(f"tool error: {exc.__cause__}") from exc
    content = getattr(result, "content", None)
    assert content, f"tool {name} returned no content"
    block = content[0]
    assert isinstance(block, types.TextContent)
    return block.text


class TestToolRegistry:
    async def test_all_tools_registered(self, mcp_server: MCPServer) -> None:
        tools = await mcp_server.list_tools()
        assert {t.name for t in tools} == EXPECTED_TOOLS

    async def test_tools_have_descriptions(self, mcp_server: MCPServer) -> None:
        tools = await mcp_server.list_tools()
        for tool in tools:
            assert tool.description, f"{tool.name} lacks a description"


class TestTools:
    async def test_list_notes(self, mcp_server: MCPServer) -> None:
        text = await call_tool(mcp_server, "list_notes", {})
        assert "[dir] projects" in text
        assert "[note] todo.md" in text

    async def test_list_notes_empty_folder(
        self, mcp_server: MCPServer, notes_root: Any
    ) -> None:
        (notes_root / "empty").mkdir()
        text = await call_tool(mcp_server, "list_notes", {"path": "empty"})
        assert text == "(empty folder)"

    async def test_read_note(self, mcp_server: MCPServer) -> None:
        assert await call_tool(mcp_server, "read_note", {"path": "todo.md"}) == "buy milk"

    async def test_read_note_error(self, mcp_server: MCPServer) -> None:
        with pytest.raises(AssertionError, match="no such note"):
            await call_tool(mcp_server, "read_note", {"path": "nope.md"})

    async def test_add_note(self, mcp_server: MCPServer) -> None:
        text = await call_tool(
            mcp_server, "add_note", {"path": "new/x.md", "content": "hi"}
        )
        assert text == "saved: new/x.md"
        assert await call_tool(mcp_server, "read_note", {"path": "new/x.md"}) == "hi"

    async def test_search_notes(self, mcp_server: MCPServer) -> None:
        text = await call_tool(mcp_server, "search_notes", {"terms": ["eurohpc"]})
        assert "2026-08-29-sync.md" in text

    async def test_search_notes_no_hits(self, mcp_server: MCPServer) -> None:
        text = await call_tool(mcp_server, "search_notes", {"terms": ["zzzz"]})
        assert text == "(no matches)"

    async def test_query_notes(
        self, mcp_server: MCPServer, fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = [
            json.dumps({"keywords": ["eurohpc"], "semantic_question": "decided?"}),
            json.dumps(
                [
                    {
                        "path": "projects/eurohpc/2026-08-01-kickoff.md",
                        "reason": "the decision",
                    }
                ]
            ),
        ]
        text = await call_tool(
            mcp_server, "query_notes", {"question": "where did we decide?"}
        )
        assert "2026-08-01-kickoff.md" in text
        assert "the decision" in text

    async def test_query_notes_no_results(
        self, mcp_server: MCPServer, fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = [json.dumps({"keywords": ["zzzz"]})]
        text = await call_tool(mcp_server, "query_notes", {"question": "zzzz?"})
        assert text == "(no matching notes)"

    async def test_convert_transcript(
        self, mcp_server: MCPServer, fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = ["# Minutes"]
        text = await call_tool(
            mcp_server,
            "convert_transcript",
            {"folder": "projects/eurohpc", "transcript": "talk", "filename": "o.md"},
        )
        assert "saved: projects/eurohpc/o.md" in text
        assert "# Minutes" in text

    async def test_cleanup_note(
        self, mcp_server: MCPServer, fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = ["# Clean"]
        text = await call_tool(mcp_server, "cleanup_note", {"path": "todo.md"})
        assert "saved: todo.md" in text

    async def test_get_context(self, mcp_server: MCPServer) -> None:
        text = await call_tool(mcp_server, "get_context", {"path": "projects/eurohpc"})
        assert "ROOT CONTEXT" in text and "PROJECTS CONTEXT" in text

    async def test_get_context_empty(
        self, mcp_server: MCPServer, notes_root: Any
    ) -> None:
        (notes_root / "AGENTS.md").unlink()
        (notes_root / "projects" / "AGENTS.md").unlink()
        text = await call_tool(mcp_server, "get_context", {})
        assert text == "(no context files on this path)"


class TestBuildServer:
    async def test_build_server_registers_tools(self, config: Config) -> None:
        server = build_server(config)
        tools = await server.list_tools()
        assert {t.name for t in tools} == EXPECTED_TOOLS

    def test_stdio_entry_point(
        self, config: Config, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mimama-mcp command parses --config and runs stdio transport."""
        from click.testing import CliRunner

        cfg_file = tmp_path / "c.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "notes": {"root": str(tmp_path), "git": False},
                    "llm": {"base_url": "http://x/v1", "api_key": "k", "model": "m"},
                    "transcripts": {"whisper": {"model": "/fake/models/ggml-test.bin"}},
                }
            ),
            encoding="utf-8",
        )
        ran: list[str] = []
        monkeypatch.setattr(
            MCPServer, "run", lambda self, transport="stdio": ran.append(transport)
        )
        result = CliRunner().invoke(main, ["--config", str(cfg_file)])
        assert result.exit_code == 0
        assert ran == ["stdio"]

    def test_stdio_entry_point_bad_config(self, tmp_path: Any) -> None:
        from click.testing import CliRunner

        bad = tmp_path / "bad.json"
        bad.write_text("{}", encoding="utf-8")
        result = CliRunner().invoke(main, ["--config", str(bad)])
        assert result.exit_code != 0
        assert "invalid" in result.output


class TestHTTPMount:
    def _mcp_initialize(self, test_client: TestClient) -> int:
        response = test_client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            headers={"accept": "application/json, text/event-stream"},
        )
        return response.status_code

    def test_mounted_when_enabled(self, config: Config) -> None:
        from mimama.server.app import create_app

        config.mcp.http_enabled = True
        app = create_app(config=config, llm=FakeLLMClient())
        with TestClient(app) as test_client:
            assert self._mcp_initialize(test_client) == 200

    def test_not_mounted_when_disabled(self, config: Config) -> None:
        from mimama.server.app import create_app

        config.mcp.http_enabled = False
        app = create_app(config=config, llm=FakeLLMClient())
        with TestClient(app) as test_client:
            assert self._mcp_initialize(test_client) == 404
