import tomllib

import pytest
from typer.testing import CliRunner

from riftguard import store
from riftguard.cli import app

runner = CliRunner()


def test_init_codex_writes_valid_toml_and_agents_md(repo):
    cfg = repo / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "x"\n')
    r = runner.invoke(app, ["init", "--harness", "codex"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["init", "--harness", "codex"])
    assert ".codex/config.toml already up to date" in r.output

    data = tomllib.loads(cfg.read_text())
    assert data["model"] == "gpt-5" and "other" in data["mcp_servers"]   # user config kept
    assert data["mcp_servers"]["riftguard"]["args"][-1] == "mcp"
    for event, sub in [("PreToolUse", "pre-tool-use"), ("PostToolUse", "post-tool-use")]:
        (group,) = data["hooks"][event]
        assert group["hooks"][0]["command"].endswith(f"riftguard hook {sub}")
    assert cfg.read_text().count("riftguard:start") == 1
    assert "check_decisions" in (repo / "AGENTS.md").read_text()
    assert not (repo / ".mcp.json").exists()                              # claude untouched


def test_init_both(repo):
    runner.invoke(app, ["init", "--harness", "both"])
    assert (repo / ".mcp.json").exists() and (repo / ".codex" / "config.toml").exists()
    assert (repo / "CLAUDE.md").exists() and (repo / "AGENTS.md").exists()


@pytest.mark.parametrize("tool,command,effect", [
    ("shell", ["bash", "-lc", "git status && ls"], "pure"),
    ("shell", ["bash", "-lc", "npm install"], "irreversible"),
    ("exec_command", "rg foo", "pure"),
    ("apply_patch", None, "reversible"),
])
def test_codex_tool_classification(tool, command, effect):
    assert store.classify(tool, {"command": command} if command else {}) == effect
