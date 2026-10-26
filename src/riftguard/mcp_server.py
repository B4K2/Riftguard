"""MCP server: lets agents record and check decisions and create checkpoints.

Claude Code launches one stdio server per session. Tools return plain text, since the
reader is a model.
"""

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from riftguard import decisions, store
from riftguard.config import judge_from_config
from riftguard.judge import Judge
from riftguard.paths import riftguard_dir, store_path, worktree_root

INSTRUCTIONS = """\
Riftguard coordinates parallel agents on this repository.
Before making an architectural choice (datastore, framework, API style, auth, major
dependency, project layout), call check_decisions with the topic. If another agent already
decided it, follow that decision instead of introducing a competing one. After making a
choice, call record_decision. Call checkpoint before risky or exploratory work."""


# Unmatched decisions shown by check_decisions(topic); keeps output small as decisions grow.
OTHERS_LIMIT = 20


class Context:
    """Resolves the store and the calling agent for the server's worktree."""

    def __init__(self, cwd: str | Path, judge: Judge | None = None):
        self.cwd = cwd
        self._judge = judge
        self.worktree = str(worktree_root(cwd))
        self.store = store_path(cwd)
        self.decisions_dir = riftguard_dir(cwd) / "decisions"

    def connect(self):
        if not self.store.exists():
            raise RuntimeError("riftguard is not initialised in this repo; run `riftguard init`")
        return store.connect(self.store)

    def judge(self) -> Judge:
        # Read config per call so edits to .riftguard/config.toml apply without a restart.
        return self._judge or judge_from_config(self.cwd)

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
        held = [d for d in decisions.pending(db) if topic is None or decisions.topics_match(topic, d.topic)]
        rejected = [d for d in decisions.all_decisions(db) if d.status == "rejected"
                    and topic is not None and decisions.topics_match(topic, d.topic)]
    finally:
        db.close()
    tail = [f"Pending review (do not build on these): {d.short()} (conflicts with #{d.conflict_with})"
            for d in held]
    tail += [f"Rejected by the developer: {d.short()}" for d in rejected]
    if topic is None:
        if not everything and not tail:
            return "No active decisions yet."
        return "\n".join(["Active decisions (get_decision(id) for reasons):"]
                         + [d.short() for d in everything] + tail)
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
    return "\n".join(lines + tail)


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
        result = decisions.record(db, topic, choice, reason, agent_id, checkpoint, ctx.judge())
        decisions.export(db, ctx.decisions_dir)
    finally:
        db.close()
    d, v = result.decision, result.verdict
    if result.pending:
        other = next((c for c in result.conflicts if c.id == v.with_id), None)
        lines = [
            f"HELD FOR REVIEW: #{d.id} [{d.topic}] {d.choice} conflicts with "
            + (other.summary() if other else f"#{v.with_id}")
            + f" (severity {v.severity}, confidence {v.confidence:.2f}, judge: {v.backend}).",
            f"Judge: {v.recommendation}",
            f"Do not build on {d.choice}. Follow the existing decision, or wait for the developer "
            f"to run `riftguard resolve {d.id} --keep <id>`.",
        ]
        return "\n".join(lines)
    lines = [f"Recorded {d.summary()}"]
    if result.superseded:
        lines.append("Superseded your earlier: " + "; ".join(x.summary() for x in result.superseded))
    if v:
        lines.append(f"Judge ({v.backend}) checked it against "
                     + ", ".join(f"#{c.id}" for c in result.conflicts)
                     + f": no conflict. {v.rationale}")
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
    server = MCPServer("riftguard", instructions=INSTRUCTIONS)

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
