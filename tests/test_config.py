"""Tests for mimama.config."""

import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from mimama.config import (
    CONFIG_ENV_VAR,
    Config,
    ConfigError,
    click_config_callback,
    load_config,
)


def valid_config_dict(notes_root: str) -> dict[str, object]:
    return {
        "notes": {
            "root": notes_root,
            "git": False,
            "git_exe": "git"
        },
        "llm": {
            "base_url": "http://llm.example/v1",
            "api_key": "sk-test",
            "model": "test-model",
        },
        "transcripts": {
            "ffmpeg": "ffmpeg",
            "whisper": {
                "cli": "whisper-cli",
                "model": "~/bin/whisper-models/ggml-large-v3-turbo.bin"
            }
        },
    }


def write_config(path: Path, data: dict[str, object]) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestLoadConfig:
    def test_minimal_valid_config(self, tmp_path: Path) -> None:
        cfg_file = write_config(tmp_path / "c.json", valid_config_dict(str(tmp_path)))
        cfg = load_config(cfg_file)
        assert cfg.notes.root == tmp_path
        assert cfg.llm.model == "test-model"

    def test_defaults_applied(self, tmp_path: Path) -> None:
        cfg_file = write_config(tmp_path / "c.json", valid_config_dict(str(tmp_path)))
        cfg = load_config(cfg_file)
        assert cfg.server.port == 8000
        assert cfg.server.url == "http://localhost:8000"
        assert cfg.listeners.discord.enabled is False
        assert cfg.mcp.http_enabled is False
        assert cfg.query.max_candidates == 20
        assert cfg.query.excerpt_chars == 1200
        assert cfg.user.name == ""
        assert cfg.user.aliases == []

    def test_full_config(self, tmp_path: Path) -> None:
        data = valid_config_dict(str(tmp_path))
        data.update(
            {
                "server": {"host": "0.0.0.0", "port": 9999, "url": "http://x:9999"},
                "user": {"name": "Erik", "aliases": ["E"]},
                "listeners": {"discord": {"enabled": True, "token": "t"}},
                "mcp": {"http_enabled": True},
                "query": {"max_candidates": 5, "excerpt_chars": 100},
            }
        )
        cfg = load_config(write_config(tmp_path / "c.json", data))
        assert cfg.server.port == 9999
        assert cfg.user.aliases == ["E"]
        assert cfg.listeners.discord.enabled is True
        assert cfg.listeners.discord.token == "t"
        assert cfg.mcp.http_enabled is True
        assert cfg.query.max_candidates == 5

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="cannot read config file"):
            load_config(tmp_path / "nope.json")

    def test_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError, match="not valid JSON"):
            load_config(bad)

    def test_invalid_schema(self, tmp_path: Path) -> None:
        bad = write_config(tmp_path / "bad.json", {"notes_root": str(tmp_path)})
        with pytest.raises(ConfigError, match="invalid"):
            load_config(bad)

    def test_missing_transcripts_config(self, tmp_path: Path) -> None:
        # Test that missing transcripts config fails validation
        data = valid_config_dict(str(tmp_path))
        # Remove transcripts section to test missing required field
        del data["transcripts"]
        bad = write_config(tmp_path / "bad.json", data)
        with pytest.raises(ConfigError, match="transcripts"):
            load_config(bad)

    def test_invalid_whisper_config(self, tmp_path: Path) -> None:
        # Test that missing whisper model fails validation
        data = valid_config_dict(str(tmp_path))
        # Remove model from whisper config to test required field
        del data["transcripts"]["whisper"]["model"]
        bad = write_config(tmp_path / "bad.json", data)
        with pytest.raises(ConfigError, match="model"):
            load_config(bad)

    def test_filename_from_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_file = write_config(tmp_path / "env.json", valid_config_dict(str(tmp_path)))
        monkeypatch.setenv(CONFIG_ENV_VAR, str(cfg_file))
        cfg = load_config()
        assert cfg.notes.root == tmp_path

    def test_default_filename_without_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
        monkeypatch.chdir(tmp_path)
        write_config(tmp_path / "mimama.json", valid_config_dict(str(tmp_path)))
        cfg = load_config()
        assert cfg.notes.root == tmp_path


class TestClickConfigCallback:
    @staticmethod
    def make_cli() -> click.Command:
        @click.command()
        @click.option("--config", callback=click_config_callback)
        def cli(config: Config) -> None:
            click.echo(f"root={config.notes.root}")

        return cli

    def test_valid_config(self, tmp_path: Path) -> None:
        cfg_file = write_config(tmp_path / "c.json", valid_config_dict(str(tmp_path)))
        result = CliRunner().invoke(self.make_cli(), ["--config", str(cfg_file)])
        assert result.exit_code == 0
        assert f"root={tmp_path}" in result.output

    def test_invalid_config_fails_cleanly(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{}", encoding="utf-8")
        result = CliRunner().invoke(self.make_cli(), ["--config", str(bad)])
        assert result.exit_code != 0
        assert "invalid" in result.output

    def test_missing_file_fails_cleanly(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            self.make_cli(), ["--config", str(tmp_path / "nope.json")]
        )
        assert result.exit_code != 0
        assert "cannot read config file" in result.output
