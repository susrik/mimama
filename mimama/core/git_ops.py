"""Git operations wrapper for staging changes when enabled.

This module provides a simple interface to run git commands in a shell,
staging files when git integration is enabled in the configuration. It is the
single seam that hides *how* git is invoked (subprocess today; a future
swap to a Python git library would only touch this module).
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mimama.config import Config

def git_enabled(config: Config) -> bool:
    """Check if git integration is enabled in config."""
    return config.notes.git


def run_git_command(config: Config, command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a git command, rooted at the configured notes directory."""
    git_exe = config.notes.git_exe
    full_command = [git_exe] + command

    return subprocess.run(
        full_command,
        cwd=config.notes.root,
        capture_output=True,
        text=True,
        check=False,  # we handle errors manually
    )


def stage_file(config: Config, filepath: str) -> bool:
    """Stage a file for commit if git integration is enabled."""
    if not git_enabled(config):
        return False

    result = run_git_command(config, ["add", "--", filepath])
    return result.returncode == 0


def stage_files(config: Config, filepaths: list[str]) -> bool:
    """Stage multiple files for commit if git integration is enabled."""
    if not git_enabled(config):
        return False
    if not filepaths:
        return True

    result = run_git_command(config, ["add", "--", *filepaths])
    return result.returncode == 0


def is_tracked(config: Config, filepath: str) -> bool:
    """Check if a file is tracked by git."""
    if not git_enabled(config):
        return False

    result = run_git_command(config, ["ls-files", "--error-unmatch", filepath])
    return result.returncode == 0
