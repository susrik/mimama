"""Tests for mimama.core.git_ops."""

import subprocess
from pathlib import Path

from mimama.config import Config
from mimama.core.git_ops import is_tracked, stage_file, stage_files


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def _staged(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return set(result.stdout.split())


class TestGitDisabled:
    def test_stage_file_noop(self, config: Config, notes_root: Path) -> None:
        config.notes.git = False
        assert stage_file(config, "todo.md") is False

    def test_stage_files_noop(self, config: Config, notes_root: Path) -> None:
        config.notes.git = False
        assert stage_files(config, ["todo.md"]) is False

    def test_is_tracked_false_when_disabled(self, config: Config, notes_root: Path) -> None:
        config.notes.git = False
        assert is_tracked(config, "todo.md") is False


class TestGitEnabled:
    def test_stage_file_runs_in_notes_root(self, config: Config, notes_root: Path) -> None:
        _init_repo(notes_root)
        config.notes.git = True
        assert stage_file(config, "todo.md") is True
        assert "todo.md" in _staged(notes_root)

    def test_stage_files_multiple(self, config: Config, notes_root: Path) -> None:
        _init_repo(notes_root)
        config.notes.git = True
        assert stage_files(config, ["todo.md", "projects/AGENTS.md"]) is True
        assert {"todo.md", "projects/AGENTS.md"} <= _staged(notes_root)

    def test_stage_files_empty_is_noop_success(self, config: Config, notes_root: Path) -> None:
        _init_repo(notes_root)
        config.notes.git = True
        assert stage_files(config, []) is True

    def test_is_tracked(self, config: Config, notes_root: Path) -> None:
        _init_repo(notes_root)
        config.notes.git = True
        assert is_tracked(config, "todo.md") is False
        stage_file(config, "todo.md")
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=notes_root, check=True)
        assert is_tracked(config, "todo.md") is True
