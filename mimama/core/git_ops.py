"""Git operations wrapper for staging changes when enabled.

This module provides a simple interface to run git commands in a shell,
staging files when git integration is enabled in the configuration.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mimama.config import Config

def git_enabled(config: Config) -> bool:
    """Check if git integration is enabled in config."""
    return config.notes.git


def run_git_command(config: Config, command: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a git command with the configured git executable."""
    git_exe = config.notes.git_exe
    full_command = [git_exe] + command

    # Run git in the specified directory or current directory
    return subprocess.run(
        full_command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False  # We handle errors manually
    )


def stage_file(config: Config, filepath: str) -> bool:
    """Stage a file for commit if git integration is enabled."""
    if not git_enabled(config):
        return False

    result = run_git_command(config, ["add", filepath])
    return result.returncode == 0


def stage_files(config: Config, filepaths: list[str]) -> bool:
    """Stage multiple files for commit if git integration is enabled."""
    if not git_enabled(config):
        return False

    # Use temporary file to pass many files at once
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('\n'.join(filepaths))
        temp_file = f.name

    try:
        result = run_git_command(config, ["add", "--stdin"], cwd=None)
        return result.returncode == 0
    finally:
        Path(temp_file).unlink(missing_ok=True)


def is_tracked(config: Config, filepath: str) -> bool:
    """Check if a file is tracked by git."""
    if not git_enabled(config):
        return False

    result = run_git_command(config, ["ls-files", "--error-unmatch", filepath])
    return result.returncode == 0
