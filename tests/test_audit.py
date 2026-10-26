from typer.testing import CliRunner

from riftguard import decisions, store
from riftguard.cli import app
from riftguard.paths import store_path

runner = CliRunner()


def _setup(repo):
    runner.invoke(app, ["init"])
    db = store.connect(store_path(repo))
    # Recorded without a judge, as if they met only at merge time.
    decisions.record(db, "database", "Postgres", "transactions", "agent-a")
    decisions.record(db, "cache", "Redis", "speed", "agent-b")
    decisions.record(db, "datastore", "MongoDB", "flexible schema", "agent-b")
    return db


def test_audit_reports_conflicts_without_holding(repo):
    db = _setup(repo)
    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 1
    assert "#3 datastore: MongoDB (agent-b) conflicts with #1 database: Postgres" in result.output
    assert "Redis" not in result.output
    assert [d.id for d in decisions.active(db)] == [1, 2, 3]


def test_audit_hold_marks_newer_pending(repo):
    db = _setup(repo)
    result = runner.invoke(app, ["audit", "--hold"])
    assert result.exit_code == 1 and "held 1 for review" in result.output
    (p,) = decisions.pending(db)
    assert (p.id, p.conflict_with) == (3, 1)
    assert runner.invoke(app, ["audit"]).exit_code == 0  # nothing left to flag


def test_audit_ignores_same_agent(repo):
    runner.invoke(app, ["init"])
    db = store.connect(store_path(repo))
    db.execute("INSERT INTO decisions (topic, choice, agent_id, ts) VALUES"
               " ('database', 'Postgres', 'a', 0), ('database', 'MySQL', 'a', 1)")
    assert runner.invoke(app, ["audit"]).output.strip() == "no conflicts"
