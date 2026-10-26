"""MCP server: lets agents record and check decisions and create checkpoints.

Claude Code launches one stdio server per session. Tools return plain text, since the
reader is a model.
"""

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from agentgit import decisions, store
from agentgit.paths import agentgit_dir, store_path, worktree_root

INSTRUCTIONS = """\
AgentGit coordinates parallel agents on this repository.
Before making an architectural choice (datastore, framework, API style, auth, major
dependency, project layout), call check_decisions with the topic. If another agent already
decided it, follow that decision instead of introducing a competing one. After making a
choice, call record_decision. Call checkpoint before risky or exploratory work."""


# Unmatched decisions shown by check_decisions(topic); keeps output small as decisions grow.
OTHERS_LIMIT = 20


class Context:
    """Resolves the store and the calling agent for the server's worktree."""

    def __init__(self, cwd: str | Path):
        self.worktree = str(worktree_root(cwd))
        self.store = store_path(cwd)
        self.decisions_dir = agentgit_dir(cwd) / "decisions"

    def connect(self):
        if not self.store.exists():
            raise RuntimeError("agentgit is not initialised in this repo; run `agentgit init`")
        return store.connect(self.store)

    def agent(self, db) -> tuple[str, str | None]:
        found = store.agent_for_worktree(db, self.worktree)
        if found:
            return found
        # No tool call recorded yet for this worktree; name the agent after it.
        return f"{Path(self.worktree).name}-unrecorded", None


def check_decisions(ctx: Context, topic: str | None = None) -> str:
    db = ctx.connect()
    try:
        found = decisions.check(db, topic)
        everything = decisions.active(db)
    finally:
        db.close()
    if topic is None:
        if not everything:
            return "No active decisions yet."
        return "Active decisions (get_decision(id) for reasons):\n" + \
            "\n".join(d.short() for d in everything)
    if found:
        lines = [f"Existing decisions matching '{topic}' (follow these):"]
        lines += [d.summary() for d in found]
    else:
        lines = [f"No active decision matches '{topic}'. You may decide it, then record_decision."]
    # Topic matching is crude, so list the rest compactly (newest first, capped) as a backstop.
    others = [d for d in reversed(everything) if d not in found]
    if others:
        lines.append("Other active decisions (check whether any are related):")
        lines += [d.short() for d in others[:OTHERS_LIMIT]]
        if len(others) > OTHERS_LIMIT:
            lines.append(f"+{len(others) - OTHERS_LIMIT} more; call check_decisions() to list all.")
    return "\n".join(lines)


def get_decision(ctx: Context, decision_id: int) -> str:
    db = ctx.connect()
    try:
        d = decisions.get(db, decision_id)
    finally:
        db.close()
    if d is None:
        return f"No decision #{decision_id}."
    return d.summary() + ("" if d.status == "active" else f" [{d.status}]")


def record_decision(ctx: Context, topic: str, choice: str, reason: str) -> str:
    db = ctx.connect()
    try:
        agent_id, head = ctx.agent(db)
        checkpoint = None
        if head:
            checkpoint = store.create_checkpoint(
                db, f"decision-{decisions.slug(topic)}", agent_id, head, note=f"{topic}: {choice}")
        result = decisions.record(db, topic, choice, reason, agent_id, checkpoint)
        decisions.export(db, ctx.decisions_dir)
    finally:
        db.close()
    lines = [f"Recorded {result.decision.summary()}"]
    if result.superseded:
        lines.append("Superseded your earlier: " + "; ".join(d.summary() for d in result.superseded))
    if result.conflicts:
        lines.append("WARNING: possible conflict with other agents' decisions:")
        lines += [d.summary() for d in result.conflicts]
        lines.append("Unless the choices are compatible, stop and align with the existing "
                     "decision or ask the developer.")
    return "\n".join(lines)


def checkpoint(ctx: Context, label: str, note: str | None = None) -> str:
    db = ctx.connect()
    try:
        agent_id, head = ctx.agent(db)
        if head is None:
            return "Nothing to checkpoint yet: no tool calls recorded for this agent."
        name = store.create_checkpoint(db, label, agent_id, head, note)
    finally:
        db.close()
    return f"Checkpoint '{name}' created at step {head[:10]} for {agent_id}."


def build_server(cwd: str | Path) -> MCPServer:
    ctx = Context(cwd)
    server = MCPServer("agentgit", instructions=INSTRUCTIONS)

    @server.tool(name="check_decisions")
    def _check(topic: str | None = None) -> str:
        """List active architectural decisions matching a topic (e.g. "database",
        "auth"). Omit topic to list all. Call before making an architectural choice."""
        return check_decisions(ctx, topic)

    @server.tool(name="record_decision")
    def _record(topic: str, choice: str, reason: str) -> str:
        """Record an architectural decision so other agents follow it. topic: short area
        name ("database"); choice: what you picked ("Postgres"); reason: why."""
        return record_decision(ctx, topic, choice, reason)

    @server.tool(name="get_decision")
    def _get(decision_id: int) -> str:
        """Full details (including the reason) of one decision by id."""
        return get_decision(ctx, decision_id)

    @server.tool(name="checkpoint")
    def _checkpoint(label: str, note: str | None = None) -> str:
        """Name the current point in your trajectory so it can be rewound to or forked from."""
        return checkpoint(ctx, label, note)

    return server


def main() -> None:
    build_server(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())).run("stdio")
