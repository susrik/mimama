"""Platform-agnostic listener protocol.

A listener adapts one chat platform (Discord, Slack, ...) to the shared
command router: it converts platform messages into :class:`IncomingMessage`,
passes them to a handler, and sends the handler's :class:`Reply` back.

Listeners are async context managers so the server can tie their lifecycle to
the app lifespan (see ``start_enabled_listeners``).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, runtime_checkable


@dataclass
class IncomingMessage:
    """A platform-independent inbound message."""

    text: str
    author: str
    channel: str


@dataclass
class Reply:
    """A platform-independent outbound reply."""

    text: str


@runtime_checkable
class Listener(Protocol):
    """Lifecycle contract every platform listener implements."""

    async def __aenter__(self) -> Listener: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
