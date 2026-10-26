"""Locate the shared store. Every linked worktree resolves to the main repo's store."""

import subprocess
from pathlib import Path


def repo_root(cwd: str | Path = ".") -> Path:
    """Main worktree root, even when called from inside a linked worktree."""
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(common).parent  # .../repo/.git -> .../repo


def agentgit_dir(cwd: str | Path = ".") -> Path:
    return repo_root(cwd) / ".agentgit"


def store_path(cwd: str | Path = ".") -> Path:
    return agentgit_dir(cwd) / "local" / "store.db"


def worktree_root(cwd: str | Path = ".") -> Path:
    """Top level of the worktree containing cwd (linked worktrees resolve to themselves)."""
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(top)
