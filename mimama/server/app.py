"""FastAPI application factory.

Wires config → core objects → routers. The GUI router is mounted alongside
/api; the MCP streamable-HTTP app is mounted at /mcp only when
``mcp.http_enabled``; enabled listeners are started with the app lifespan.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from mimama.config import Config, load_config
from mimama.core.inference_jobs import InferenceJobRegistry
from mimama.core.llm import LLMClient, SupportsComplete
from mimama.core.notes import NoteStore
from mimama.core.transcribe import TranscriptionJobRegistry


def create_app(
    config: Config | None = None, llm: SupportsComplete | None = None
) -> FastAPI:
    """Build the app. ``config``/``llm`` are injectable for tests."""
    if config is None:
        config = load_config()
    if llm is None:
        llm = LLMClient(config.llm)
    store = NoteStore(root=config.notes.root)

    # imported here to avoid import cycles (routers import nothing from app)
    from mimama.listeners import start_enabled_listeners
    from mimama.mcp.server import create_http_app
    from mimama.server.api import router as api_router
    from mimama.server.gui import router as gui_router

    mcp_server = create_http_app(config) if config.mcp.http_enabled else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            if mcp_server is not None:
                # mounted sub-app lifespans don't run automatically; run the
                # MCP session manager with the outer app's lifespan instead
                await stack.enter_async_context(mcp_server.session_manager.run())
            await start_enabled_listeners(config, stack)
            yield

    app = FastAPI(title="mimama", lifespan=lifespan)
    app.state.config = config
    app.state.store = store
    app.state.llm = llm
    app.state.transcription_jobs = TranscriptionJobRegistry()
    app.state.inference_jobs = InferenceJobRegistry()

    app.include_router(api_router)
    app.include_router(gui_router)

    if mcp_server is not None:
        from mcp.server.transport_security import TransportSecuritySettings

        app.mount(
            "/mcp",
            mcp_server.streamable_http_app(
                streamable_http_path="/",
                # mimama is a trusted-network tool that may be reached via
                # arbitrary hostnames (Docker, LAN); disable Host pinning.
                transport_security=TransportSecuritySettings(
                    enable_dns_rebinding_protection=False
                ),
            ),
        )

    return app
