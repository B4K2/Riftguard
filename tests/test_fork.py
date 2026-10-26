import io
import json

import pytest
from typer.testing import CliRunner

from riftguard import decisions, hooks, store
from riftguard.cli import app
from riftguard.paths import store_path

runner = CliRunner()


def _post(cwd, tool, tool_input, session="sess-aaaa1111"):
    ev = {"session_id": session, "cwd": str(cwd), "tool_name": tool,
          "tool_input": tool_input, "tool_response": {}}
    hooks.post_tool_use(io.StringIO(json.dumps(ev)))


@pytest.fixture
def db(repo):
    runner.invoke(app, ["init"])
    db = store.connect(store_path(repo))
    _post(repo, "TodoWrite", {"todos": [{"content": "add API", "status": "in_progress"}]})
    _post(repo, "Read", {"file_path": "README.md"})
    _post(repo, "Edit", {"file_path": "app.py", "old_string": "a", "new_string": "b"})
    agent, head = db.execute("SELECT id, head FROM agents").fetchone()
    store.create_checkpoint(db, "cp", agent, head, "API half done")
    _post(repo, "Bash", {"command": "pip install pymongo"})  # after the checkpoint
    decisions.record(db, "database", "Postgres", "transactions", agent)
    return db


def test_fork_prints_summary(db, repo):
    result = runner.invoke(app, ["fork", "cp", "--why", "avoid MongoDB"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "checkpoint `cp` (API half done)" in out
    assert "Why this fork: avoid MongoDB" in out
    assert "[database] Postgres" in out
    assert "[in_progress] add API" in out
    assert "Files it edited:\n- app.py" in out
    assert "Files it read:\n- README.md" in out
    assert "pymongo" not in out  # happened after the checkpoint


def test_forked_session_claims_agent_and_shares_history(db, repo, monkeypatch):
    source, cp_step = db.execute(
        "SELECT agent_id, step_hash FROM checkpoints WHERE name='cp'").fetchone()
    runner.invoke(app, ["fork", "cp"])
    fork_id = f"{source}-fork1"
    assert db.execute("SELECT session_id, head, forked_from FROM agents WHERE id=?",
                      (fork_id,)).fetchone() == (None, cp_step, cp_step)

    monkeypatch.setenv(hooks.FORK_ENV, fork_id)
    _post(repo, "Read", {"file_path": "app.py"}, session="sess-fork0001")
    head = db.execute("SELECT head FROM agents WHERE id=?", (fork_id,)).fetchone()[0]
    chain = list(store.walk(db, head))
    assert chain[1].hash == cp_step and len(chain) == 4  # 1 new + 3 shared steps
    assert {s.agent_id for s in chain[1:]} == {source}

    # Already claimed: another session with the same env gets its own agent.
    _post(repo, "Read", {"file_path": "x"}, session="sess-other002")
    assert db.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 3


def test_fork_numbering_and_errors(db, repo):
    runner.invoke(app, ["fork", "cp"])
    assert "-fork2 from" in runner.invoke(app, ["fork", "cp"]).output
    assert runner.invoke(app, ["fork", "missing"]).exit_code == 1
    assert runner.invoke(app, ["fork", "cp", "--launch", "vim"]).exit_code == 1


def test_fork_worktree(db, repo, tmp_path):
    result = runner.invoke(app, ["fork", "cp", "--worktree", str(tmp_path / "wt-fork")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "wt-fork" / "README.md").exists()
