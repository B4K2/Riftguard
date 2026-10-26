import json

from typer.testing import CliRunner

from agentgit import decisions, store
from agentgit.cli import app
from agentgit.mcp_server import Context, check_decisions, checkpoint, record_decision
from agentgit.paths import store_path

runner = CliRunner()


def test_topic_matching():
    assert decisions.topics_match("database", "Which datastore to use")
    assert decisions.topics_match("auth", "authentication provider")
    assert not decisions.topics_match("database", "frontend framework")
    assert not decisions.topics_match("the choice of", "a library")  # stopwords only
    assert not decisions.topics_match("MCP SDK", "agent identity in MCP server")
    assert decisions.topics_match("MCP SDK", "mcp sdk version")


def test_headline_scenario_agent_b_learns_of_agent_a(repo, tmp_path, replay):
    """Agent A picks Postgres; agent B in another worktree is told before choosing MongoDB."""
    runner.invoke(app, ["init"])
    wt_b = tmp_path / "wt-b"
    replay("read", repo)   # agent A (sess-aaaa1111) works in the main worktree
    replay("bash", wt_b)   # agent B (sess-bbbb2222) works in wt-b
    a, b = Context(repo), Context(wt_b)

    out = record_decision(a, "database", "Postgres", "relational data, need transactions")
    assert "Recorded" in out and "WARNING" not in out

    out = check_decisions(b, "datastore")
    assert "Postgres" in out and "follow these" in out

    out = record_decision(b, "datastore", "MongoDB", "flexible schema")
    assert "WARNING: possible conflict" in out and "Postgres" in out

    db = store.connect(store_path(repo))
    by_agent = {d.agent_id: d.choice for d in decisions.active(db)}
    assert by_agent == {"repo-sess-aaa": "Postgres", "wt-b-sess-bbb": "MongoDB"}


def test_same_agent_supersedes_its_own_decision(repo, replay):
    runner.invoke(app, ["init"])
    replay("read", repo)
    ctx = Context(repo)
    record_decision(ctx, "database", "SQLite", "simple")
    out = record_decision(ctx, "database", "Postgres", "need concurrency")
    assert "Superseded your earlier" in out and "WARNING" not in out
    db = store.connect(store_path(repo))
    assert [d.choice for d in decisions.active(db)] == ["Postgres"]


def test_decision_links_checkpoint_and_exports_markdown(repo, replay):
    runner.invoke(app, ["init"])
    replay("read", repo)
    record_decision(Context(repo), "API style", "REST", "simple clients")
    db = store.connect(store_path(repo))
    (d,) = decisions.active(db)
    (step,) = db.execute("SELECT step_hash FROM checkpoints WHERE name=?", (d.checkpoint,)).fetchone()
    assert step == db.execute("SELECT head FROM agents").fetchone()[0]
    files = list((repo / ".agentgit" / "decisions").glob("*.md"))
    assert [f.name for f in files] == ["0001-api-style.md"]
    assert "choice: REST" in files[0].read_text()


def test_check_lists_unmatched_decisions_too(repo, replay):
    runner.invoke(app, ["init"])
    replay("read", repo)
    ctx = Context(repo)
    record_decision(ctx, "frontend", "React", "team knows it")
    out = check_decisions(ctx, "database")
    assert "No active decision matches" in out and "React" in out


def test_checkpoint_tool(repo, replay):
    runner.invoke(app, ["init"])
    ctx = Context(repo)
    assert "Nothing to checkpoint" in checkpoint(ctx, "before-refactor")
    replay("read", repo)
    assert "'before-refactor'" in checkpoint(ctx, "before-refactor")
    assert "'before-refactor-2'" in checkpoint(ctx, "before-refactor")


def test_init_registers_mcp_and_claude_md_idempotently(repo):
    (repo / "CLAUDE.md").write_text("# My project\n\nExisting notes.\n")
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["init"])
    assert "MCP server already registered" in r.output
    assert "CLAUDE.md already up to date" in r.output
    md = (repo / "CLAUDE.md").read_text()
    assert md.startswith("# My project") and md.count("agentgit:start") == 1
    mcp = json.loads((repo / ".mcp.json").read_text())
    assert mcp["mcpServers"]["agentgit"]["args"][-1] == "mcp"


def test_cli_decide_conflicts_with_agent_decision(repo, replay):
    runner.invoke(app, ["init"])
    replay("read", repo)
    record_decision(Context(repo), "database", "Postgres", "transactions")
    r = runner.invoke(app, ["decide", "db", "MySQL", "-r", "hosting"])
    assert "possible conflict" in r.output
    r = runner.invoke(app, ["decisions"])
    assert "Postgres" in r.output and "MySQL" in r.output
    r = runner.invoke(app, ["supersede", "2"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["decisions"])
    assert "MySQL" not in r.output


def test_check_output_stays_compact(repo, replay):
    from agentgit.mcp_server import OTHERS_LIMIT, get_decision
    runner.invoke(app, ["init"])
    replay("read", repo)
    ctx = Context(repo)
    long_reason = "a long justification " * 10
    for i in range(OTHERS_LIMIT + 5):
        record_decision(ctx, f"area{i}", f"choice{i}", long_reason)
    out = check_decisions(ctx, "database")
    assert long_reason not in out                      # unmatched: no reasons
    assert "+5 more" in out
    assert f"#{OTHERS_LIMIT + 5} area{OTHERS_LIMIT + 4}: choice" in out  # newest first
    assert long_reason.strip() in get_decision(ctx, 1)
    assert "No decision #999" in get_decision(ctx, 999)


def test_init_writes_portable_commands_when_installed_as_tool(repo, monkeypatch):
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr("agentgit.cli.shutil.which", lambda name: "/usr/local/bin/agentgit")
    runner.invoke(app, ["init"])
    mcp = json.loads((repo / ".mcp.json").read_text())
    assert mcp["mcpServers"]["agentgit"] == {"command": "agentgit", "args": ["mcp"]}
    settings = json.loads((repo / ".claude" / "settings.json").read_text())
    cmd = settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert cmd == "agentgit hook post-tool-use"
