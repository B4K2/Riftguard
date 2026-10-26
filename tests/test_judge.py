import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentgit import decisions, store
from agentgit.cli import app
from agentgit.decisions import Decision
from agentgit.judge import (ClaudeJudge, CodexJudge, HeuristicJudge, Verdict, get_judge,
                            judge_with_fallback)
from agentgit.mcp_server import Context, check_decisions, record_decision
from agentgit.paths import store_path

runner = CliRunner()
POSTGRES = Decision(1, "database", "Postgres", "transactions", "agent-a", None, "active", 0.0)
VERDICT = {"conflict": True, "with_id": 1, "severity": "high", "confidence": 0.93,
           "recommendation": "Keep #1 Postgres.", "rationale": "Two primary datastores."}


class FakeJudge:
    name = "fake"

    def __init__(self, conflict):
        self.conflict, self.calls = conflict, []

    def judge(self, topic, choice, reason, candidates):
        self.calls.append((topic, choice, [c.id for c in candidates]))
        if self.conflict:
            return Verdict(True, candidates[0].id, "high", 0.9, "Keep the first.", "clash", self.name)
        return Verdict(False, None, "none", 0.9, "No conflict.", "Different concerns.", self.name)


def _two_agents(repo, tmp_path, replay, judge):
    runner.invoke(app, ["init"])
    replay("read", repo)
    replay("bash", tmp_path / "wt-b")
    return Context(repo, judge), Context(tmp_path / "wt-b", judge)


def test_judge_only_runs_when_there_are_candidates(repo, tmp_path, replay):
    j = FakeJudge(conflict=True)
    a, b = _two_agents(repo, tmp_path, replay, j)
    record_decision(a, "database", "Postgres", "tx")
    record_decision(b, "frontend", "React", "ui")       # no matching topic: judge not called
    assert j.calls == []
    record_decision(b, "datastore", "MongoDB", "schema")
    assert j.calls == [("datastore", "MongoDB", [1])]


def test_compatible_choices_stay_active(repo, tmp_path, replay):
    a, b = _two_agents(repo, tmp_path, replay, FakeJudge(conflict=False))
    record_decision(a, "database", "Postgres", "tx")
    out = record_decision(b, "database cache", "Redis", "speed")
    assert out.startswith("Recorded") and "no conflict" in out
    db = store.connect(store_path(repo))
    assert {d.choice for d in decisions.active(db)} == {"Postgres", "Redis"}


@pytest.mark.parametrize("keep_new,expect", [(False, ("Postgres", "rejected")),
                                             (True, ("MongoDB", "superseded"))])
def test_resolve(repo, tmp_path, replay, keep_new, expect):
    a, b = _two_agents(repo, tmp_path, replay, FakeJudge(conflict=True))
    record_decision(a, "database", "Postgres", "tx")
    record_decision(b, "datastore", "MongoDB", "schema")
    r = runner.invoke(app, ["conflicts"])
    assert "#2 datastore: MongoDB" in r.output and "--keep 1" in r.output
    r = runner.invoke(app, ["resolve", "2", "--keep", "2" if keep_new else "1"])
    assert r.exit_code == 0, r.output
    db = store.connect(store_path(repo))
    assert [d.choice for d in decisions.active(db)] == [expect[0]]
    loser = decisions.get(db, 1 if keep_new else 2)
    assert loser.status == expect[1]
    if not keep_new:
        assert "Rejected by the developer: #2 datastore: MongoDB" in check_decisions(b, "database")
    assert runner.invoke(app, ["resolve", "2", "--keep", "1"]).exit_code == 1  # already settled


def test_heuristic_judge():
    v = HeuristicJudge().judge("db", "MongoDB", None, [POSTGRES])
    assert v.conflict and v.with_id == 1
    assert not HeuristicJudge().judge("db", "postgres", None, [POSTGRES]).conflict


def test_broken_judge_falls_back_to_heuristic():
    class Broken:
        name = "broken"

        def judge(self, *a):
            raise RuntimeError("boom")

    v = judge_with_fallback(Broken(), "db", "MongoDB", None, [POSTGRES])
    assert v.conflict and v.backend == "heuristic" and "broken judge failed: boom" in v.rationale


def _fake_cli(bin_dir: Path, name: str, script: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    p = bin_dir / name
    p.write_text("#!/usr/bin/env python3\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


ARGV_LOG = """
import json, os, sys
prompt = sys.stdin.read()
log = {"argv": sys.argv[1:], "cwd": os.getcwd(), "judge_env": os.environ.get("AGENTGIT_JUDGE"),
       "has_prompt": "Postgres" in prompt and "MongoDB" in prompt}
open(os.environ["FAKE_LOG"], "w").write(json.dumps(log))
"""


def test_claude_backend_invocation(tmp_path, monkeypatch):
    _fake_cli(tmp_path / "bin", "claude", ARGV_LOG + f"""
print(json.dumps({{"type": "result", "result": "", "structured_output": {VERDICT!r}}}))
""")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "log.json"))
    v = ClaudeJudge().judge("datastore", "MongoDB", "schema", [POSTGRES])
    assert (v.conflict, v.with_id, v.backend) == (True, 1, "claude")
    log = json.loads((tmp_path / "log.json").read_text())
    argv = log["argv"]
    assert argv[0] == "-p" and "--json-schema" in argv and "--strict-mcp-config" in argv
    assert argv[argv.index("--tools") + 1] == "" and "user" in argv
    assert log["judge_env"] == "1" and log["has_prompt"]
    assert "agentgit-judge-" in log["cwd"]            # isolated from the repo


def test_codex_backend_invocation(tmp_path, monkeypatch):
    _fake_cli(tmp_path / "bin", "codex", ARGV_LOG + f"""
a = sys.argv[1:]
assert a[0] == "exec" and a[-1] == "-"
json.load(open(a[a.index("--output-schema") + 1]))   # schema file must be valid JSON
open(a[a.index("-o") + 1], "w").write(json.dumps({VERDICT!r}))
""")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "log.json"))
    v = CodexJudge(model="gpt-5-mini").judge("datastore", "MongoDB", "schema", [POSTGRES])
    assert (v.conflict, v.with_id, v.backend) == (True, 1, "codex")
    log = json.loads((tmp_path / "log.json").read_text())
    for flag in ["--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "-m"]:
        assert flag in log["argv"]
    assert log["judge_env"] == "1" and log["has_prompt"]


def test_auto_backend_picks_installed_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert get_judge("auto").name == "heuristic"
    _fake_cli(tmp_path / "bin", "codex", "")
    assert get_judge("auto").name == "codex"
    _fake_cli(tmp_path / "bin", "claude", "")
    assert get_judge("auto").name == "claude"
    with pytest.raises(ValueError):
        get_judge("gpt")


def test_hooks_ignore_judge_sessions(repo, replay, monkeypatch):
    store.init_store(store_path(repo))
    monkeypatch.setenv("AGENTGIT_JUDGE", "1")
    replay("read", repo)
    db = store.connect(store_path(repo))
    assert db.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 0
