"""Settings from .riftguard/config.toml (committed)."""

import os
import tomllib
from pathlib import Path

from riftguard.judge import Judge, get_judge
from riftguard.paths import riftguard_dir


def load(cwd: str | Path = ".") -> dict:
    path = riftguard_dir(cwd) / "config.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {}


def judge_from_config(cwd: str | Path = ".") -> Judge:
    """RIFTGUARD_JUDGE_BACKEND overrides the file. [judge] backend = "auto" | "claude" | "codex" | "heuristic"; model = "..." (optional)."""
    section = load(cwd).get("judge", {})
    backend = os.environ.get("RIFTGUARD_JUDGE_BACKEND") or section.get("backend", "auto")
    return get_judge(backend, section.get("model") or None)
