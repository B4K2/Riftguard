import json

from typer.testing import CliRunner

from agentgit.cli import app

runner = CliRunner()


def test_init_is_idempotent_and_preserves_settings(repo):
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"model": "opus", "hooks": {"Stop": []}}))

    r = runner.invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    assert "installed hooks" in r.output
    r = runner.invoke(app, ["init"])
    assert "hooks already installed" in r.output

    s = json.loads(settings.read_text())
    assert s["model"] == "opus" and "Stop" in s["hooks"]
    assert len(s["hooks"]["PostToolUse"]) == 1 and len(s["hooks"]["PreToolUse"]) == 1
    assert (repo / ".agentgit" / "config.toml").exists()
    assert ".agentgit/local/" in (repo / ".gitignore").read_text()


def test_log_status_show(repo, replay):
    runner.invoke(app, ["init"])
    replay("read", repo)
    replay("edit", repo)

    r = runner.invoke(app, ["status"])
    assert "2 steps" in r.output

    r = runner.invoke(app, ["log", "--tool", "Edit"])
    assert "Edit" in r.output and "Read" not in r.output
    step_prefix = r.output.split()[0]

    r = runner.invoke(app, ["show", step_prefix])
    assert r.exit_code == 0, r.output
    assert '"new_string": "demo app"' in r.output


def test_commands_fail_cleanly_before_init(repo):
    r = runner.invoke(app, ["log"])
    assert r.exit_code == 1
