import sqlite3

import pytest
from typer.testing import CliRunner

from riftguard import decisions, store
from riftguard.cli import app
from riftguard.paths import store_path

runner = CliRunner()


def _step(db, repo, tool, inp, ts, session="sess-aaaa1111"):
    return store.record_step(db, session, str(repo), tool, inp, None, ts=ts)


@pytest.fixture
def db(repo):
    runner.invoke(app, ["init"])
    return store.connect(store_path(repo))


def _head(db):
    return db.execute("SELECT head FROM agents").fetchone()[0]


def test_rewind_moves_head_and_branches(db, repo):
    good = _step(db, repo, "Read", {"file_path": "a"}, 1)
    store.create_checkpoint(db, "clean", db.execute("SELECT id FROM agents").fetchone()[0], good)
    _step(db, repo, "Read", {"file_path": "poisoned"}, 2)
    _step(db, repo, "Bash", {"command": "pip install pymongo"}, 3)

    result = runner.invoke(app, ["rewind", "clean", "-r", "poisoned read"])
    assert result.exit_code == 0, result.output
    assert "dropping 2 steps" in result.output
    assert "pip install pymongo" in result.output  # irreversible warning
    assert _head(db) == good

    nxt = _step(db, repo, "Read", {"file_path": "b"}, 4)
    assert store.get_step(db, nxt).parent == good  # new branch from the checkpoint
    assert "poisoned read" in runner.invoke(app, ["rewinds"]).output


def test_rewind_twice_is_noop(db, repo):
    good = _step(db, repo, "Read", {"file_path": "a"}, 1)
    agent = db.execute("SELECT id FROM agents").fetchone()[0]
    store.create_checkpoint(db, "cp", agent, good)
    _step(db, repo, "Edit", {"file_path": "a"}, 2)
    assert len(store.rewind(db, "cp").dropped) == 1
    assert store.rewind(db, "cp").dropped == []
    assert db.execute("SELECT COUNT(*) FROM rewinds").fetchone()[0] == 1


def test_rewind_flags_later_decisions(db, repo):
    good = _step(db, repo, "Read", {"file_path": "a"}, 1)
    agent = db.execute("SELECT id FROM agents").fetchone()[0]
    store.create_checkpoint(db, "cp", agent, good)
    _step(db, repo, "Read", {"file_path": "b"}, 2)
    decisions.record(db, "database", "MongoDB", None, agent)
    db.execute("UPDATE decisions SET ts=2.5")
    result = runner.invoke(app, ["rewind", "cp"])
    assert "#1 database: MongoDB was decided after the checkpoint" in result.output


def test_rewind_errors(db, repo):
    assert runner.invoke(app, ["rewind", "nope"]).exit_code == 1
    a = _step(db, repo, "Read", {"file_path": "a"}, 1)
    agent = db.execute("SELECT id FROM agents").fetchone()[0]
    store.create_checkpoint(db, "cp", agent, a)
    _step(db, repo, "Read", {"file_path": "b"}, 2)
    store.rewind(db, "cp")
    _step(db, repo, "Read", {"file_path": "c"}, 3)
    _step(db, repo, "Read", {"file_path": "d"}, 4)
    # A checkpoint on the abandoned branch is no longer on the chain.
    db.execute("INSERT INTO checkpoints SELECT 'gone', hash, agent_id, NULL, 0 FROM steps"
               " WHERE ts=2")
    result = runner.invoke(app, ["rewind", "gone"])
    assert result.exit_code == 1 and "not on" in result.output


def test_old_store_gets_rewinds_table(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(store.SCHEMA.replace(store.REWINDS_TABLE + ";\n", ""))
    old.close()
    db = store.connect(path)
    assert db.execute("SELECT COUNT(*) FROM rewinds").fetchone()[0] == 0
