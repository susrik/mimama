"""Configuration: pydantic model parsed from a JSON file.

The server reads the filename from the ``MIMAMA_CONFIG_FILENAME`` environment
variable (default ``mimama.json``). The CLI and the stdio MCP entry point take
a ``--config`` option validated by :func:`click_config_callback`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import click
from pydantic import BaseModel, Field, ValidationError

DEFAULT_CONFIG_FILENAME = "mimama.json"
CONFIG_ENV_VAR = "MIMAMA_CONFIG_FILENAME"


class TranscriptsConfig(BaseModel):
    """Configuration for audio transcription tools."""

    ffmpeg: str = "ffmpeg"
    """Path to ffmpeg executable. Default is just 'ffmpeg' to let shell find it."""

    whisper: WhisperConfig = Field(default_factory=lambda: WhisperConfig(cli="whisper-cli"))


class WhisperConfig(BaseModel):
    """Configuration for whisper-cli tool."""

    cli: str = "whisper-cli"
    """Path to whisper-cli executable. Default is just 'whisper-cli' to let shell find it."""

    model: str | None = None
    """Path to the whisper model file (e.g. ~/bin/whisper-models/ggml-large-v3-turbo.bin)."""


class NotesConfig(BaseModel):
    """Configuration for notes storage."""

    root: Path
    """Path to the notes root directory."""

    git: bool = False
    """Enable git integration for automatic staging of changes."""

    git_exe: str = "git"
    """Path to git executable. Default is just 'git' to let shell find it."""


class LLMConfig(BaseModel):
    """Any OpenAI-protocol endpoint (e.g. a litellm server)."""

    base_url: str
    api_key: str
    model: str


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    url: str = "http://localhost:8000"
    """Base URL clients (CLI, listeners, MCP tools) use to reach the server."""


class UserConfig(BaseModel):
    """Identity of the primary user, so queries like "mentioning me" work."""

    name: str = ""
    aliases: list[str] = Field(default_factory=list)


class DiscordConfig(BaseModel):
    enabled: bool = False
    token: str = ""


class ListenersConfig(BaseModel):
    discord: DiscordConfig = Field(default_factory=DiscordConfig)


class MCPConfig(BaseModel):
    http_enabled: bool = False
    """Mount the MCP streamable-HTTP endpoint at /mcp on the app server."""


class QueryConfig(BaseModel):
    """Token-cost bounds for the natural-language query pipeline."""

    max_candidates: int = 20
    excerpt_chars: int = 1200


class Config(BaseModel):
    notes: NotesConfig
    llm: LLMConfig
    server: ServerConfig = Field(default_factory=ServerConfig)
    user: UserConfig = Field(default_factory=UserConfig)
    listeners: ListenersConfig = Field(default_factory=ListenersConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    query: QueryConfig = Field(default_factory=QueryConfig)


class ConfigError(Exception):
    """Raised when the config file is missing, unreadable, or invalid."""


def load_config(filename: str | Path | None = None) -> Config:
    """Load and validate the JSON config file.

    ``filename`` defaults to ``$MIMAMA_CONFIG_FILENAME`` or ``mimama.json``.
    """
    if filename is None:
        filename = os.environ.get(CONFIG_ENV_VAR, DEFAULT_CONFIG_FILENAME)
    path = Path(filename)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file {path} is not valid JSON: {exc}") from exc
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"config file {path} is invalid:\n{exc}") from exc


def click_config_callback(
    ctx: click.Context, param: click.Parameter, value: str
) -> Config:
    """click callback for ``--config``: parse the file, fail with a clean error."""
    try:
        return load_config(value)
    except ConfigError as exc:
        raise click.BadParameter(str(exc), ctx=ctx, param=param) from exc
