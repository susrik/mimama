"""Pluggable message listeners (Discord in v1; Slack etc. later)."""

from __future__ import annotations

from contextlib import AsyncExitStack

from mimama.config import Config


async def start_enabled_listeners(config: Config, stack: AsyncExitStack) -> None:
    """Start every listener enabled in the config, tied to the app lifespan."""
    if config.listeners.discord.enabled:
        from mimama.listeners.discord import DiscordListener

        listener = DiscordListener(
            token=config.listeners.discord.token, server_url=config.server.url
        )
        await stack.enter_async_context(listener)
