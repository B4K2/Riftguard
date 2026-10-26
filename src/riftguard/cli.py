"""riftguard command-line interface."""

import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import typer

from riftguard import decisions, hooks, store
from riftguard.paths import riftguard_dir, repo_root, store_path, worktree_root

app = typer.Typer(help="Version control and coordination for AI coding agents.", no_args_is_help=True)
hook_app = typer.Typer(help="Hook entry points (called by Claude Code, not by you).")
app.add_typer(hook_app, name="hook")

CONFIG_TOML = """\
# Riftguard settings (committed)
version = 1

[judge]
# Who decides whether two decisions conflict. "auto" uses Claude Code if installed, else
# Codex, else a keyword heuristic. Runs headlessly on your existing login; no API key needed.
backend = "auto"   # auto | claude | codex | heuristic
model = ""         # optional, e.g. "haiku" for claude
"""


CLAUDE_MD_START = "<!-- riftguard:start -->"
CLAUDE_MD_END = "<!-- riftguard:end -->"
CLAUDE_MD_BLOCK = f"""{CLAUDE_MD_START}
## Riftguard (multi-agent coordination)

This repo uses Riftguard. Other agents may be working in parallel worktrees.

- Before any architectural choice (datastore, framework, API style, auth, major dependency,
  project layout), call the `check_decisions` MCP tool with the topic. If a decision exists,
  follow it; do not introduce a competing choice.
- After making such a choice, call `record_decision(topic, choice, reason)`.
- If `record_decision` warns about a conflict, stop and align or ask the developer.
- Call `checkpoint(label)` before risky or exploratory work.
- Use these MCP tools for decisions; don't read files under `.riftguard/` directly.
{CLAUDE_MD_END}
"""


def _portable() -> bool:
    """True when riftguard is installed as a tool (uv tool / pipx) and on PATH, so configs can
    say plain `riftguard`. Inside a dev venv (`uv run`), pin the interpreter path instead."""
    return shutil.which("riftguard") is not None and "VIRTUAL_ENV" not in os.environ


HOOK_EVENTS = {"PreToolUse": "pre-tool-use", "PostToolUse": "post-tool-use"}


def _hook_command(sub: str) -> str:
    if _portable():
        return f"riftguard hook {sub}"
    return f'"{sys.executable}" -m riftguard hook {sub}'


def _install_mcp(root: Path) -> bool:
    """Register the riftguard MCP server in .mcp.json. Returns True if it changed."""
    path = root / ".mcp.json"
    config = json.loads(path.read_text()) if path.exists() else {}
    entry = ({"command": "riftguard", "args": ["mcp"]} if _portable()
             else {"command": sys.executable, "args": ["-m", "riftguard", "mcp"]})
    servers = config.setdefault("mcpServers", {})
    if servers.get("riftguard") == entry:
        return False
    servers["riftguard"] = entry
    path.write_text(json.dumps(config, indent=2) + "\n")
    return True


def _install_instructions(root: Path, filename: str) -> bool:
    """Add (or refresh) the riftguard section of CLAUDE.md / AGENTS.md. True if it changed."""
    path = root / filename
    text = path.read_text() if path.exists() else ""
    if CLAUDE_MD_START in text:
        head, rest = text.split(CLAUDE_MD_START, 1)
        tail = rest.split(CLAUDE_MD_END, 1)[1].lstrip("\n") if CLAUDE_MD_END in rest else ""
        new = head + CLAUDE_MD_BLOCK + tail
    else:
        new = text + ("\n" if text and not text.endswith("\n") else "") + \
            ("\n" if text else "") + CLAUDE_MD_BLOCK
    if new == text:
        return False
    path.write_text(new)
    return True


def _install_hooks(root: Path) -> bool:
    """Merge our hooks into .claude/settings.json. Returns True if anything changed."""
    settings_path = root / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    changed = False
    for event, sub in HOOK_EVENTS.items():
        groups = settings.setdefault("hooks", {}).setdefault(event, [])
        command = _hook_command(sub)
        ours = [h for group in groups for h in group.get("hooks", [])
                if f"riftguard hook {sub}" in h.get("command", "")]
        if ours:
            if ours[0]["command"] != command:
                ours[0]["command"] = command  # interpreter moved; refresh it
                changed = True
        else:
            groups.append({"matcher": "*", "hooks": [{"type": "command", "command": command}]})
            changed = True
    if changed:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return changed


CODEX_START = "# riftguard:start (managed by `riftguard init`)"
CODEX_END = "# riftguard:end"


def _codex_block() -> str:
    if _portable():
        mcp_cmd, mcp_args = "riftguard", ["mcp"]
    else:
        mcp_cmd, mcp_args = sys.executable, ["-m", "riftguard", "mcp"]
    lines = [CODEX_START, "[mcp_servers.riftguard]", f"command = {json.dumps(mcp_cmd)}",
             f"args = {json.dumps(mcp_args)}", ""]
    for event, sub in HOOK_EVENTS.items():
        lines += [f"[[hooks.{event}]]", 'matcher = "*"', f"[[hooks.{event}.hooks]]",
                  'type = "command"', f"command = {json.dumps(_hook_command(sub))}", ""]
    return "\n".join(lines + [CODEX_END]) + "\n"


def _install_codex(root: Path) -> bool:
    """Write the MCP server and hooks into .codex/config.toml. True if it changed.

    Our block goes at the end of the file: TOML tables run until the next header, so a block
    in the middle would capture the user's keys that follow it."""
    path = root / ".codex" / "config.toml"
    text = path.read_text() if path.exists() else ""
    if CODEX_START in text:
        head, rest = text.split(CODEX_START, 1)
        tail = rest.split(CODEX_END, 1)[1].lstrip("\n") if CODEX_END in rest else ""
        text_wo = head.rstrip("\n") + ("\n" + tail if tail else "")
    else:
        text_wo = text
    new = (text_wo.rstrip("\n") + "\n\n" if text_wo.strip() else "") + _codex_block()
    if new == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new)
    return True


def _harnesses(choice: str, root: Path) -> list[str]:
    if choice == "both":
        return ["claude", "codex"]
    if choice in ("claude", "codex"):
        return [choice]
    found = [h for h in ("claude", "codex") if shutil.which(h) or (root / f".{h}").exists()]
    return found or ["claude"]


def _ensure_gitignore(root: Path) -> None:
    gi = root / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    if ".riftguard/local/" not in lines:
        lines.append(".riftguard/local/")
        gi.write_text("\n".join(lines) + "\n")


def _open() -> sqlite3.Connection:
    try:
        path = store_path()
    except Exception:
        typer.echo("error: not inside a git repository", err=True)
        raise typer.Exit(1)
    if not path.exists():
        typer.echo("error: riftguard not initialised here; run `riftguard init`", err=True)
        raise typer.Exit(1)
    return store.connect(path)


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


@app.command()
def init(
    harness: str = typer.Option("auto", "--harness",
                                help="claude | codex | both | auto (whichever is installed)."),
) -> None:
    """Create .riftguard/ in the main repo and wire up Claude Code and/or Codex."""
    try:
        root = repo_root()
    except Exception:
        typer.echo("error: not inside a git repository", err=True)
        raise typer.Exit(1)
    d = riftguard_dir()
    (d / "decisions").mkdir(parents=True, exist_ok=True)
    (d / "decisions" / ".gitkeep").touch()
    if not (d / "config.toml").exists():
        (d / "config.toml").write_text(CONFIG_TOML)
    store.init_store(store_path())
    _ensure_gitignore(root)
    typer.echo(f"initialised {d}")
    targets = _harnesses(harness, root)
    if "claude" in targets:
        typer.echo("installed hooks in .claude/settings.json" if _install_hooks(root)
                   else "hooks already installed")
        typer.echo("registered MCP server in .mcp.json" if _install_mcp(root)
                   else "MCP server already registered")
        typer.echo("added riftguard section to CLAUDE.md" if _install_instructions(root, "CLAUDE.md")
                   else "CLAUDE.md already up to date")
    if "codex" in targets:
        typer.echo("wrote MCP server and hooks to .codex/config.toml" if _install_codex(root)
                   else ".codex/config.toml already up to date")
        typer.echo("added riftguard section to AGENTS.md" if _install_instructions(root, "AGENTS.md")
                   else "AGENTS.md already up to date")
        typer.echo("codex: trust this project and approve the hooks (/hooks) on first run")


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
        steps = (store.Step(*r) for r in db.execute(f"SELECT {store.STEP_COLUMNS} FROM steps ORDER BY ts DESC"))
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


@app.command("decisions")
def list_decisions(
    all_: bool = typer.Option(False, "--all", help="Include superseded decisions."),
    topic: str = typer.Option(None, "--topic", help="Only decisions matching this topic."),
) -> None:
    """List architectural decisions."""
    db = _open()
    found = decisions.all_decisions(db) if all_ else decisions.check(db, None)
    if topic:
        found = [d for d in found if decisions.topics_match(topic, d.topic)]
    if not found:
        typer.echo("no decisions")
    for d in found:
        mark = "" if d.status == "active" else f"  ({d.status})"
        typer.echo(d.summary() + mark)


@app.command()
def decide(
    topic: str,
    choice: str,
    reason: str = typer.Option(None, "--reason", "-r"),
    agent: str = typer.Option("developer", "--as", help="Who is deciding."),
) -> None:
    """Record a decision by hand (agents use the MCP tool instead). The developer's word is
    final, so this is never held for review; conflicts are only reported."""
    db = _open()
    result = decisions.record(db, topic, choice, reason, agent)
    decisions.export(db, riftguard_dir() / "decisions")
    typer.echo(f"recorded {result.decision.summary()}")
    for d in result.superseded:
        typer.echo(f"superseded {d.summary()}")
    for d in result.conflicts:
        typer.echo(f"warning: possible conflict with {d.summary()}", err=True)


@app.command()
def conflicts() -> None:
    """List decisions held for review, with the judge's verdict."""
    db = _open()
    held = decisions.pending(db)
    if not held:
        typer.echo("no pending conflicts")
    for d in held:
        other = decisions.get(db, d.conflict_with) if d.conflict_with else None
        v = json.loads(d.verdict) if d.verdict else {}
        typer.echo(f"#{d.id} {d.topic}: {d.choice} ({d.agent_id})")
        typer.echo(f"  conflicts with #{other.id} {other.topic}: {other.choice} ({other.agent_id})"
                   if other else "  conflicts with an unknown decision")
        if v:
            typer.echo(f"  judge {v.get('backend')}: severity {v.get('severity')}, "
                       f"confidence {v.get('confidence', 0):.2f}")
            typer.echo(f"  {v.get('recommendation')}")
            typer.echo(f"  why: {v.get('rationale')}")
        typer.echo(f"  resolve: riftguard resolve {d.id} --keep {d.conflict_with}   (or --keep {d.id})")


@app.command()
def resolve(
    decision_id: int,
    keep: int = typer.Option(..., "--keep", help="Id of the decision to keep."),
) -> None:
    """Settle a pending conflict by choosing which decision stands."""
    db = _open()
    try:
        kept, dropped = decisions.resolve(db, decision_id, keep)
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    decisions.export(db, riftguard_dir() / "decisions")
    typer.echo(f"kept {kept.short()}; {dropped.short()} is now {dropped.status}")


@app.command("supersede")
def supersede_cmd(decision_id: int) -> None:
    """Mark a decision as superseded."""
    db = _open()
    if not decisions.supersede(db, decision_id):
        typer.echo(f"error: no active decision #{decision_id}", err=True)
        raise typer.Exit(1)
    decisions.export(db, riftguard_dir() / "decisions")
    typer.echo(f"superseded #{decision_id}")


@app.command("checkpoint")
def checkpoint_cmd(
    label: str,
    agent: str = typer.Option(None, "--agent", "-a",
                              help="Defaults to the latest agent in this worktree."),
    note: str = typer.Option(None, "--note"),
) -> None:
    """Name an agent's current step."""
    db = _open()
    if agent:
        row = db.execute("SELECT id, head FROM agents WHERE id=?", (agent,)).fetchone()
    else:
        row = store.agent_for_worktree(db, str(worktree_root()))
    if not row or not row[1]:
        typer.echo("error: no recorded agent steps to checkpoint", err=True)
        raise typer.Exit(1)
    name = store.create_checkpoint(db, label, row[0], row[1], note)
    typer.echo(f"checkpoint {name} -> {row[1][:10]} ({row[0]})")


@app.command()
def mcp() -> None:
    """Run the MCP server over stdio (launched by Claude Code)."""
    from riftguard.mcp_server import main
    main()


@app.command("checkpoints")
def list_checkpoints(
    agent: str = typer.Option(None, "--agent", "-a"),
    auto: bool = typer.Option(True, "--auto/--no-auto", help="Include automatic checkpoints."),
) -> None:
    """List checkpoints, newest first."""
    db = _open()
    rows = db.execute(
        "SELECT name, step_hash, agent_id, note, ts FROM checkpoints"
        " WHERE (? IS NULL OR agent_id = ?) ORDER BY ts DESC", (agent, agent)).fetchall()
    rows = [r for r in rows if auto or not r[0].startswith("auto-")]
    if not rows:
        typer.echo("no checkpoints")
    for name, step, agent_id, note, ts in rows:
        typer.echo(f"{_fmt_ts(ts)}  {name:<28} {step[:10]}  {agent_id:<24} {note or ''}")


@hook_app.command("pre-tool-use")
def pre_tool_use() -> None:
    hooks.pre_tool_use()


@hook_app.command("post-tool-use")
def post_tool_use() -> None:
    hooks.post_tool_use()
