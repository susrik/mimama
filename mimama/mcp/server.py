"""MCP server exposure: stdio entry point and streamable-HTTP ASGI app.

The tools are defined once in :mod:`mimama.mcp.tools`; this module only wires
them to a transport. Both transports talk HTTP to the running mimama server.
"""

from __future__ import annotations

import click
from mcp.server.mcpserver import MCPServer

from mimama.api_client import APIClient
from mimama.config import Config, click_config_callback
from mimama.mcp.tools import register_tools

_INSTRUCTIONS = """\
mimama manages a tree of markdown meeting notes. Use query_notes for
natural-language questions (dates, people, "the meeting where...");
search_notes for cheap exact term matching; convert_transcript / cleanup_note
for LLM note generation; list_notes / read_note / add_note for direct access.
"""


def build_server(config: Config) -> MCPServer:
    server = MCPServer(name="mimama", instructions=_INSTRUCTIONS)
    register_tools(server, APIClient(config.server.url))
    return server


def create_http_app(config: Config) -> MCPServer:
    """MCPServer for mounting into the FastAPI app.

    The caller mounts ``server.streamable_http_app(streamable_http_path="/")``
    at ``/mcp`` and runs ``server.session_manager.run()`` in its lifespan
    (mounted sub-app lifespans do not run automatically).
    """
    return build_server(config)


@click.command()
@click.option(
    "--config",
    "config",
    default="mimama.json",
    callback=click_config_callback,
    help="Path to the mimama JSON config file.",
)
def main(config: Config) -> None:
    """Run the mimama MCP server over stdio (spawned by MCP clients)."""
    build_server(config).run(transport="stdio")
