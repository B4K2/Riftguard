"""Decision log: architectural decisions agents declare and check before acting.

The SQLite store is the live source of truth (shared by every worktree); markdown files
in .agentgit/decisions/ are an export for review and git history.
"""

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentgit.judge import Judge, Verdict

# Words that carry no meaning for topic matching ("which database to use" ~ "database").
STOPWORDS = {
    "a", "an", "the", "of", "for", "to", "and", "or", "in", "on", "which", "what",
    "use", "using", "choice", "choose", "layer", "system", "library", "lib",
}

# Rough synonym groups so "datastore" finds "database". The judge (phase 3) replaces this.
SYNONYMS = [
    {"database", "db", "datastore", "storage", "persistence", "store"},
    {"auth", "authentication", "login", "identity"},
    {"api", "rest", "graphql", "rpc", "endpoint", "endpoints"},
    {"frontend", "ui", "client", "web"},
    {"queue", "messaging", "broker", "pubsub", "events"},
    {"cache", "caching"},
    {"test", "tests", "testing"},
    {"deploy", "deployment", "hosting", "infra", "infrastructure"},
]


@dataclass
class Decision:
    id: int
    topic: str
    choice: str
    reason: str | None
    agent_id: str | None
    checkpoint: str | None
    status: str
    ts: float
    conflict_with: int | None = None
    verdict: str | None = None

    def summary(self) -> str:
        who = self.agent_id or "unknown"
        why = f" (reason: {self.reason})" if self.reason else ""
        return f"#{self.id} [{self.topic}] {self.choice} by {who}{why}"

    def short(self) -> str:
        return f"#{self.id} {self.topic}: {self.choice}"


@dataclass
class RecordResult:
    decision: Decision
    superseded: list[Decision]   # this agent's own earlier decisions on the topic
    conflicts: list[Decision]    # other agents' active decisions on a matching topic
    verdict: "Verdict | None" = None  # set when a judge looked at the conflicts

    @property
    def pending(self) -> bool:
        return self.decision.status == "pending"


# Each synonym maps to one fixed representative of its group ("datastore", "database" -> "db").
_CANONICAL = {w: sorted(group)[0] for group in SYNONYMS for w in group}


def _terms(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {_CANONICAL.get(w, w) for w in words if w not in STOPWORDS}


def topics_match(a: str, b: str) -> bool:
    """Most of the shorter topic's terms appear in the other. One shared word out of several
    ("MCP SDK" vs "agent identity in MCP server") is not a match."""
    ta, tb = _terms(a), _terms(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) > 0.5


# Explicit column list (never SELECT *): a store migrated by a newer agentgit must still be
# readable by an older process, e.g. an MCP server started before an upgrade.
COLUMNS = "id, topic, choice, reason, agent_id, checkpoint, status, ts, conflict_with, verdict"


def _row(r) -> Decision:
    return Decision(*r)


def active(db: sqlite3.Connection) -> list[Decision]:
    return [_row(r) for r in db.execute(
        f"SELECT {COLUMNS} FROM decisions WHERE status='active' ORDER BY id")]


def get(db: sqlite3.Connection, decision_id: int) -> Decision | None:
    r = db.execute(f"SELECT {COLUMNS} FROM decisions WHERE id=?", (decision_id,)).fetchone()
    return _row(r) if r else None


def all_decisions(db: sqlite3.Connection) -> list[Decision]:
    return [_row(r) for r in db.execute(f"SELECT {COLUMNS} FROM decisions ORDER BY id")]


def check(db: sqlite3.Connection, topic: str | None = None) -> list[Decision]:
    """Active decisions whose topic matches; all active decisions if topic is None."""
    decisions = active(db)
    if topic is None:
        return decisions
    return [d for d in decisions if topics_match(topic, d.topic)]


def record(
    db: sqlite3.Connection,
    topic: str,
    choice: str,
    reason: str | None,
    agent_id: str | None,
    checkpoint: str | None = None,
    judge: "Judge | None" = None,
) -> RecordResult:
    """Record a decision. Supersedes the same agent's earlier decision on a matching topic.

    Other agents' matching decisions are candidates. Without a judge they are only reported.
    With one, a confirmed conflict stores the new decision as pending until the developer
    resolves it."""
    from agentgit.judge import judge_with_fallback

    candidates = [d for d in active(db) if topics_match(topic, d.topic) and d.agent_id != agent_id]
    # Judge outside the write transaction: it can take seconds, and hooks need the lock.
    verdict = None
    if judge and candidates:
        verdict = judge_with_fallback(judge, topic, choice, reason, candidates)
    pending = bool(verdict and verdict.conflict)
    db.execute("BEGIN IMMEDIATE")
    try:
        mine = [] if pending else [
            d for d in active(db) if topics_match(topic, d.topic) and d.agent_id == agent_id]
        for d in mine:
            db.execute("UPDATE decisions SET status='superseded' WHERE id=?", (d.id,))
        cur = db.execute(
            "INSERT INTO decisions (topic, choice, reason, agent_id, checkpoint, status, ts,"
            " conflict_with, verdict) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (topic, choice, reason, agent_id, checkpoint, "pending" if pending else "active",
             time.time(), verdict.with_id if pending else None,
             verdict.to_json() if verdict else None),
        )
        new = get(db, cur.lastrowid)
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return RecordResult(new, mine, candidates, verdict)


def pending(db: sqlite3.Connection) -> list[Decision]:
    return [_row(r) for r in db.execute(
        f"SELECT {COLUMNS} FROM decisions WHERE status='pending' ORDER BY id")]


def resolve(db: sqlite3.Connection, pending_id: int, keep_id: int) -> tuple[Decision, Decision]:
    """Settle a pending conflict. keep_id is either the pending decision (it becomes active and
    the one it clashed with is superseded) or the existing one (the pending one is rejected).
    Returns (kept, dropped)."""
    p = get(db, pending_id)
    if p is None or p.status != "pending":
        raise ValueError(f"#{pending_id} is not a pending decision")
    if keep_id not in (p.id, p.conflict_with):
        raise ValueError(f"keep must be #{p.id} or #{p.conflict_with}")
    db.execute("BEGIN IMMEDIATE")
    try:
        if keep_id == p.id:
            db.execute("UPDATE decisions SET status='active' WHERE id=?", (p.id,))
            db.execute("UPDATE decisions SET status='superseded' WHERE id=?", (p.conflict_with,))
        else:
            db.execute("UPDATE decisions SET status='rejected' WHERE id=?", (p.id,))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    kept, dropped = get(db, keep_id), get(db, p.conflict_with if keep_id == p.id else p.id)
    return kept, dropped


def supersede(db: sqlite3.Connection, decision_id: int) -> bool:
    cur = db.execute(
        "UPDATE decisions SET status='superseded' WHERE id=? AND status='active'", (decision_id,))
    return cur.rowcount == 1


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "decision"


def to_markdown(d: Decision) -> str:
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d.ts))
    return (
        "---\n"
        f"id: {d.id}\ntopic: {d.topic}\nchoice: {d.choice}\nstatus: {d.status}\n"
        f"agent: {d.agent_id or ''}\ncheckpoint: {d.checkpoint or ''}\ndate: {when}\n"
        "---\n\n"
        f"# {d.topic}: {d.choice}\n\n{d.reason or ''}\n"
    )


def export(db: sqlite3.Connection, directory: Path) -> list[Path]:
    """Write one markdown file per decision (NNNN-topic.md), replacing stale exports."""
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("[0-9][0-9][0-9][0-9]-*.md"):
        old.unlink()
    paths = []
    for d in all_decisions(db):
        p = directory / f"{d.id:04d}-{slug(d.topic)}.md"
        p.write_text(to_markdown(d))
        paths.append(p)
    return paths
