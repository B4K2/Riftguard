"""Content-addressed object store backed by one SQLite file.

Kept behind this module so the storage layer can be swapped (or ported to Rust) later.
"""

import hashlib
import json
import sqlite3
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
  hash  TEXT PRIMARY KEY,
  body  BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
  id           TEXT PRIMARY KEY,
  session_id   TEXT UNIQUE,
  worktree     TEXT NOT NULL,
  head         TEXT,
  forked_from  TEXT
);
CREATE TABLE IF NOT EXISTS steps (
  hash        TEXT PRIMARY KEY,
  agent_id    TEXT NOT NULL REFERENCES agents(id),
  parent      TEXT,
  tool        TEXT NOT NULL,
  input_hash  TEXT NOT NULL REFERENCES objects(hash),
  output_hash TEXT REFERENCES objects(hash),
  effect      TEXT NOT NULL,
  ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS steps_agent ON steps(agent_id, ts);
CREATE TABLE IF NOT EXISTS checkpoints (
  name       TEXT PRIMARY KEY,
  step_hash  TEXT NOT NULL REFERENCES steps(hash),
  agent_id   TEXT NOT NULL,
  note       TEXT,
  ts         REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
  id          INTEGER PRIMARY KEY,
  topic       TEXT NOT NULL,
  choice      TEXT NOT NULL,
  reason      TEXT,
  agent_id    TEXT,
  checkpoint  TEXT,
  status      TEXT NOT NULL DEFAULT 'active',
  ts          REAL NOT NULL
);
"""

# Side-effect class per tool. Unknown tools (including MCP tools) default to
# irreversible so rewind errs on the side of warning.
EFFECTS = {
    "Read": "pure", "Grep": "pure", "Glob": "pure", "LS": "pure",
    "WebSearch": "pure", "WebFetch": "pure", "TodoWrite": "pure",
    "Edit": "reversible", "MultiEdit": "reversible", "Write": "reversible",
    "NotebookEdit": "reversible",
}


@dataclass
class Step:
    hash: str
    agent_id: str
    parent: str | None
    tool: str
    input_hash: str
    output_hash: str | None
    effect: str
    ts: float


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def connect(path: Path) -> sqlite3.Connection:
    # isolation_level=None: we manage transactions explicitly (BEGIN IMMEDIATE).
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def init_store(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = connect(path)
    db.executescript(SCHEMA)
    db.close()


def put_object(db: sqlite3.Connection, obj: Any) -> str:
    raw = canonical(obj)
    h = sha256(raw)
    db.execute("INSERT OR IGNORE INTO objects VALUES (?, ?)", (h, zlib.compress(raw)))
    return h


def get_object(db: sqlite3.Connection, h: str) -> Any:
    row = db.execute("SELECT body FROM objects WHERE hash=?", (h,)).fetchone()
    if row is None:
        raise KeyError(h)
    return json.loads(zlib.decompress(row[0]))


def default_agent_id(session_id: str, worktree: str) -> str:
    return f"{Path(worktree).name}-{session_id[:8]}"


def record_step(
    db: sqlite3.Connection,
    session_id: str,
    worktree: str,
    tool: str,
    tool_input: Any,
    tool_output: Any,
    ts: float | None = None,
) -> str:
    """Append one tool call to the session's chain and advance its head. Returns the step hash."""
    ts = time.time() if ts is None else ts
    db.execute("BEGIN IMMEDIATE")  # serialize head updates across concurrent hooks
    try:
        row = db.execute(
            "SELECT id, head FROM agents WHERE session_id=?", (session_id,)
        ).fetchone()
        if row is None:
            agent_id, parent = default_agent_id(session_id, worktree), None
            db.execute(
                "INSERT INTO agents (id, session_id, worktree) VALUES (?, ?, ?)",
                (agent_id, session_id, worktree),
            )
        else:
            agent_id, parent = row
        inp = put_object(db, tool_input)
        out = put_object(db, tool_output)
        step = sha256(canonical([parent, agent_id, tool, inp, out, ts]))
        db.execute(
            "INSERT INTO steps VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (step, agent_id, parent, tool, inp, out, EFFECTS.get(tool, "irreversible"), ts),
        )
        db.execute("UPDATE agents SET head=? WHERE id=?", (step, agent_id))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return step


def get_step(db: sqlite3.Connection, prefix: str) -> Step:
    rows = db.execute(
        "SELECT * FROM steps WHERE hash LIKE ? LIMIT 2", (prefix + "%",)
    ).fetchall()
    if not rows:
        raise KeyError(f"no step matching {prefix}")
    if len(rows) > 1:
        raise KeyError(f"ambiguous step prefix {prefix}")
    return Step(*rows[0])


def walk(db: sqlite3.Connection, head: str | None) -> Iterator[Step]:
    """Yield steps from head back to the root, following parent pointers (like git log)."""
    while head:
        row = db.execute("SELECT * FROM steps WHERE hash=?", (head,)).fetchone()
        if row is None:
            return
        step = Step(*row)
        yield step
        head = step.parent


def agent_for_worktree(db: sqlite3.Connection, worktree: str) -> tuple[str, str | None] | None:
    """(agent_id, head) of the most recently active agent in a worktree, if any.

    The MCP server has no session id, so this is how it learns who is calling. Two
    concurrent sessions in one worktree are ambiguous; agentgit expects one agent per worktree.
    """
    return db.execute(
        """SELECT a.id, a.head FROM agents a LEFT JOIN steps s ON s.hash = a.head
           WHERE a.worktree = ? ORDER BY COALESCE(s.ts, 0) DESC LIMIT 1""",
        (worktree,),
    ).fetchone()


def create_checkpoint(
    db: sqlite3.Connection, label: str, agent_id: str, step_hash: str, note: str | None = None
) -> str:
    """Name the agent's current step. Returns the stored name (suffixed if label is taken)."""
    name, n = label, 1
    while db.execute("SELECT 1 FROM checkpoints WHERE name=?", (name,)).fetchone():
        n += 1
        name = f"{label}-{n}"
    db.execute(
        "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?)",
        (name, step_hash, agent_id, note, time.time()),
    )
    return name
