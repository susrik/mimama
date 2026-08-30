"""Discord listener: thin discord.py wiring around the shared CommandRouter.

The bot reacts to messages starting with "!" (see router.HELP_TEXT), calls the
mimama server over HTTP, and replies in the same channel. All command logic
lives in :mod:`mimama.listeners.router` so other platforms can reuse it.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import TracebackType

import discord

from mimama.api_client import APIClient
from mimama.listeners.base import IncomingMessage
from mimama.listeners.router import CommandRouter


class DiscordListener:
    """Runs a discord.py client as a background task (async context manager)."""

    def __init__(self, token: str, server_url: str) -> None:
        self._token = token
        self._router = CommandRouter(APIClient(server_url))
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._task: asyncio.Task[None] | None = None

        @self._client.event
        async def on_message(message: discord.Message) -> None:
            await self.on_message(message)

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self._client.user:
            return
        incoming = IncomingMessage(
            text=message.content,
            author=str(message.author),
            channel=str(message.channel),
        )
        # the router does blocking HTTP; keep the event loop free
        reply = await asyncio.to_thread(self._router.handle, incoming)
        if reply is not None:
            await message.channel.send(reply.text)

    async def __aenter__(self) -> DiscordListener:
        self._task = asyncio.create_task(self._client.start(self._token))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.close()
        if self._task is not None:
            with contextlib.suppress(discord.DiscordException, asyncio.CancelledError):
                await self._task
