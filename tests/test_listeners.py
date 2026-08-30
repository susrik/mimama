"""Tests for the listener protocol, command router, and Discord wiring."""

import json
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mimama.api_client import APIClient
from mimama.config import Config
from mimama.listeners import start_enabled_listeners
from mimama.listeners.base import IncomingMessage, Listener
from mimama.listeners.router import HELP_TEXT, CommandRouter
from tests.fakes import FakeLLMClient
from tests.test_cli import _TestClientTransport


@pytest.fixture()
def router(client: TestClient) -> CommandRouter:
    api = APIClient("http://testserver", transport=_TestClientTransport(client))
    return CommandRouter(api)


def msg(text: str) -> IncomingMessage:
    return IncomingMessage(text=text, author="tester", channel="general")


class TestRouterDispatch:
    def test_non_command_ignored(self, router: CommandRouter) -> None:
        assert router.handle(msg("hello there")) is None

    def test_help(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!help"))
        assert reply is not None and reply.text == HELP_TEXT

    def test_unknown_command(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!bogus"))
        assert reply is not None and "unknown command" in reply.text

    def test_ls(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!ls"))
        assert reply is not None
        assert "projects" in reply.text and "todo.md" in reply.text

    def test_ls_subfolder(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!ls projects/eurohpc"))
        assert reply is not None and "2026-08-29-sync.md" in reply.text

    def test_show(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!show todo.md"))
        assert reply is not None and reply.text == "buy milk"

    def test_show_usage(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!show"))
        assert reply is not None and "usage" in reply.text

    def test_search(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!search eurohpc"))
        assert reply is not None and "2026-08-29-sync.md" in reply.text

    def test_search_no_hits(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!search zzzz"))
        assert reply is not None and "(no matches)" in reply.text

    def test_search_usage(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!search"))
        assert reply is not None and "usage" in reply.text

    def test_query(self, router: CommandRouter, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = [json.dumps({"keywords": ["eurohpc"]})]
        reply = router.handle(msg("!query eurohpc notes"))
        assert reply is not None and "2026-08-29-sync.md" in reply.text

    def test_query_no_results(
        self, router: CommandRouter, fake_llm: FakeLLMClient
    ) -> None:
        fake_llm.responses = [json.dumps({"keywords": ["zzzz"]})]
        reply = router.handle(msg("!query zzzz"))
        assert reply is not None and "no matching notes" in reply.text

    def test_query_usage(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!query"))
        assert reply is not None and "usage" in reply.text

    def test_cleanup(self, router: CommandRouter, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = ["# Clean"]
        reply = router.handle(msg("!cleanup todo.md"))
        assert reply is not None and "saved: todo.md" in reply.text

    def test_cleanup_usage(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!cleanup"))
        assert reply is not None and "usage" in reply.text

    def test_convert(self, router: CommandRouter, fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = ["# Minutes"]
        reply = router.handle(msg("!convert projects/eurohpc\nraw transcript text"))
        assert reply is not None
        assert "saved: projects/eurohpc/" in reply.text
        assert "# Minutes" in reply.text

    def test_convert_usage_without_body(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!convert projects/eurohpc"))
        assert reply is not None and "usage" in reply.text

    def test_api_error_becomes_reply(self, router: CommandRouter) -> None:
        reply = router.handle(msg("!show nope.md"))
        assert reply is not None and "error:" in reply.text

    def test_long_reply_clipped(self, router: CommandRouter,
                                fake_llm: FakeLLMClient) -> None:
        fake_llm.responses = ["x" * 5000]
        reply = router.handle(msg("!cleanup todo.md"))
        assert reply is not None and len(reply.text) <= 1800


class TestListenerProtocol:
    def test_discord_listener_satisfies_protocol(self) -> None:
        from mimama.listeners.discord import DiscordListener

        assert isinstance(
            DiscordListener(token="t", server_url="http://x"), Listener
        )


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)

    def __str__(self) -> str:
        return "general"


class _FakeMessage:
    def __init__(self, content: str, author: str = "tester") -> None:
        self.content = content
        self.author = author
        self.channel = _FakeChannel()


class TestDiscordListener:
    @pytest.fixture()
    def listener(self, client: TestClient) -> Any:
        from mimama.listeners.discord import DiscordListener

        instance = DiscordListener(token="t", server_url="http://x")
        instance._router = CommandRouter(
            APIClient("http://testserver", transport=_TestClientTransport(client))
        )
        return instance

    async def test_command_replied_in_channel(self, listener: Any) -> None:
        message = _FakeMessage("!show todo.md")
        await listener.on_message(message)
        assert message.channel.sent == ["buy milk"]

    async def test_non_command_no_reply(self, listener: Any) -> None:
        message = _FakeMessage("just chatting")
        await listener.on_message(message)
        assert message.channel.sent == []

    async def test_own_messages_ignored(self, listener: Any) -> None:
        message = _FakeMessage("!help")
        message.author = listener._client.user  # simulate the bot itself
        await listener.on_message(message)
        assert message.channel.sent == []


class TestStartEnabledListeners:
    async def test_nothing_started_when_disabled(self, config: Config) -> None:
        async with AsyncExitStack() as stack:
            await start_enabled_listeners(config, stack)  # no error, no-op

    async def test_discord_started_when_enabled(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started: list[tuple[str, str]] = []

        class FakeDiscordListener:
            def __init__(self, token: str, server_url: str) -> None:
                started.append((token, server_url))

            async def __aenter__(self) -> "FakeDiscordListener":
                return self

            async def __aexit__(
                self,
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                tb: TracebackType | None,
            ) -> None:
                pass

        import mimama.listeners.discord as discord_module

        monkeypatch.setattr(discord_module, "DiscordListener", FakeDiscordListener)
        config.listeners.discord.enabled = True
        config.listeners.discord.token = "tok"
        async with AsyncExitStack() as stack:
            await start_enabled_listeners(config, stack)
        assert started == [("tok", config.server.url)]


class TestDiscordLifecycle:
    async def test_start_and_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """__aenter__ starts the discord client; __aexit__ closes it."""
        import discord as discord_pkg

        from mimama.listeners.discord import DiscordListener

        events: list[str] = []

        async def fake_start(self: Any, token: str) -> None:
            events.append(f"start:{token}")

        async def fake_close(self: Any) -> None:
            events.append("close")

        monkeypatch.setattr(discord_pkg.Client, "start", fake_start)
        monkeypatch.setattr(discord_pkg.Client, "close", fake_close)

        async with DiscordListener(token="tok", server_url="http://x"):
            pass
        # start runs in a background task, so relative order isn't guaranteed
        assert sorted(events) == ["close", "start:tok"]

    async def test_exit_swallows_discord_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import discord as discord_pkg

        from mimama.listeners.discord import DiscordListener

        async def failing_start(self: Any, token: str) -> None:
            raise discord_pkg.LoginFailure("bad token")

        async def fake_close(self: Any) -> None:
            pass

        monkeypatch.setattr(discord_pkg.Client, "start", failing_start)
        monkeypatch.setattr(discord_pkg.Client, "close", fake_close)

        async with DiscordListener(token="bad", server_url="http://x"):
            pass  # the login failure must not propagate out of __aexit__

    async def test_on_message_event_registered(self, client: TestClient) -> None:
        """The decorated on_message event forwards to the handler."""
        from mimama.listeners.discord import DiscordListener

        listener = DiscordListener(token="t", server_url="http://x")
        listener._router = CommandRouter(
            APIClient("http://testserver", transport=_TestClientTransport(client))
        )
        message = _FakeMessage("!show todo.md")
        await listener._client.on_message(message)  # type: ignore[attr-defined]
        assert message.channel.sent == ["buy milk"]
