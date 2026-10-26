import io
import os
import subprocess
from pathlib import Path

import pytest

from agentgit import hooks

FIXTURES = Path(__file__).parent / "fixtures"


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(autouse=True)
def heuristic_judge(monkeypatch):
    """Never shell out to a real claude/codex from tests."""
    monkeypatch.setenv("AGENTGIT_JUDGE_BACKEND", "heuristic")
    monkeypatch.delenv("AGENTGIT_JUDGE", raising=False)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """A git repo with one commit and a linked worktree at <tmp>/wt-b."""
    root = tmp_path / "repo"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "t", cwd=root)
    (root / "README.md").write_text("# demo\n")
    git("add", ".", cwd=root)
    git("commit", "-qm", "init", cwd=root)
    git("worktree", "add", "-q", "-b", "agent-b", str(tmp_path / "wt-b"), cwd=root)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def replay():
    """Feed a recorded hook payload to the hook, as if from Claude Code, with cwd rewritten."""
    def _replay(name: str, cwd: Path) -> None:
        payload = (FIXTURES / f"{name}.json").read_text().replace("__CWD__", str(cwd))
        hooks.post_tool_use(io.StringIO(payload))
    return _replay
