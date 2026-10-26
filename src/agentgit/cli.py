"""agentgit command-line interface."""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import typer

from agentgit import hooks, store
from agentgit.paths import agentgit_dir, repo_root, store_path

app = typer.Typer(help="Version control and coordination for AI coding agents.", no_args_is_help=True)
hook_app = typer.Typer(help="Hook entry points (called by Claude Code, not by you).")
app.add_typer(hook_app, name="hook")

CONFIG_TOML = """\
# AgentGit settings (committed)
version = 1

[agents]
harness = "claude-code"
"""


def _hook_command() -> str:
    # Absolute interpreter path so the hook works without agentgit on PATH.
    return f'"{sys.executable}" -m agentgit hook post-tool-use'


def _install_hook(root: Path) -> bool:
    """Merge our PostToolUse hook into .claude/settings.json. Returns True if it changed."""
    settings_path = root / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    post = settings.setdefault("hooks", {}).setdefault("PostToolUse", [])
    command = _hook_command()
    ours = [h for group in post for h in group.get("hooks", [])
            if "agentgit hook post-tool-use" in h.get("command", "")]
    if ours:
        if ours[0]["command"] == command:
            return False
        ours[0]["command"] = command  # interpreter moved; refresh it
    else:
        post.append({"matcher": "*", "hooks": [{"type": "command", "command": command}]})
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return True


def _ensure_gitignore(root: Path) -> None:
    gi = root / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    if ".agentgit/local/" not in lines:
        lines.append(".agentgit/local/")
        gi.write_text("\n".join(lines) + "\n")


def _open() -> sqlite3.Connection:
    try:
        path = store_path()
    except Exception:
        typer.echo("error: not inside a git repository", err=True)
        raise typer.Exit(1)
    if not path.exists():
        typer.echo("error: agentgit not initialised here; run `agentgit init`", err=True)
        raise typer.Exit(1)
    return store.connect(path)


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


@app.command()
def init() -> None:
    """Create .agentgit/ in the main repo and install the Claude Code hook."""
    try:
        root = repo_root()
    except Exception:
        typer.echo("error: not inside a git repository", err=True)
        raise typer.Exit(1)
    d = agentgit_dir()
    (d / "decisions").mkdir(parents=True, exist_ok=True)
    (d / "decisions" / ".gitkeep").touch()
    if not (d / "config.toml").exists():
        (d / "config.toml").write_text(CONFIG_TOML)
    store.init_store(store_path())
    _ensure_gitignore(root)
    changed = _install_hook(root)
    typer.echo(f"initialised {d}")
    typer.echo("installed PostToolUse hook in .claude/settings.json" if changed
               else "hook already installed")


@app.command()
def status() -> None:
    """List agents with their worktree, step count and last activity."""
    db = _open()
    rows = db.execute(
        """SELECT a.id, a.worktree, COUNT(s.hash), MAX(s.ts)
           FROM agents a LEFT JOIN steps s ON s.agent_id = a.id
           GROUP BY a.id ORDER BY MAX(s.ts) DESC"""
    ).fetchall()
    if not rows:
        typer.echo("no agents recorded yet")
        return
    for agent_id, worktree, n, last in rows:
        last_s = _fmt_ts(last) if last else "-"
        typer.echo(f"{agent_id:<28} {n:>5} steps  last {last_s}  {worktree}")


@app.command()
def log(
    agent: str = typer.Option(None, "--agent", "-a", help="Only this agent's chain."),
    tool: str = typer.Option(None, "--tool", "-t", help="Only steps using this tool."),
    limit: int = typer.Option(50, "--limit", "-n"),
) -> None:
    """Show recorded steps, newest first."""
    db = _open()
    if agent:
        row = db.execute("SELECT head FROM agents WHERE id=?", (agent,)).fetchone()
        if row is None:
            typer.echo(f"error: unknown agent {agent}", err=True)
            raise typer.Exit(1)
        steps = store.walk(db, row[0])
    else:
        steps = (store.Step(*r) for r in db.execute("SELECT * FROM steps ORDER BY ts DESC"))
    shown = 0
    for s in steps:
        if tool and s.tool != tool:
            continue
        typer.echo(f"{s.hash[:10]}  {_fmt_ts(s.ts)}  {s.agent_id:<24} {s.tool:<12} [{s.effect}]")
        shown += 1
        if shown >= limit:
            break


@app.command()
def show(step: str) -> None:
    """Show one step's input and output (hash prefix accepted)."""
    db = _open()
    try:
        s = store.get_step(db, step)
    except KeyError as e:
        typer.echo(f"error: {e.args[0]}", err=True)
        raise typer.Exit(1)
    typer.echo(f"step    {s.hash}\nparent  {s.parent or '-'}\nagent   {s.agent_id}")
    typer.echo(f"tool    {s.tool} [{s.effect}]\ntime    {_fmt_ts(s.ts)}\n")
    typer.echo("input:\n" + json.dumps(store.get_object(db, s.input_hash), indent=2))
    if s.output_hash:
        typer.echo("\noutput:\n" + json.dumps(store.get_object(db, s.output_hash), indent=2))


@hook_app.command("post-tool-use")
def post_tool_use() -> None:
    hooks.post_tool_use()
