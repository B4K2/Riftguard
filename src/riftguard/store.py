"""Content-addressed object store backed by one SQLite file.

Kept behind this module so the storage layer can be swapped (or ported to Rust) later.
"""

import hashlib
import json
import re
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
  status      TEXT NOT NULL DEFAULT 'active',  -- active | superseded | pending | rejected
  ts          REAL NOT NULL,
  conflict_with INTEGER,                        -- set while pending: the decision it clashes with
  verdict     TEXT                              -- judge verdict JSON
);
"""

# Columns added after the first release; init_store adds them to older stores.
MIGRATIONS = [("decisions", "conflict_with", "INTEGER"), ("decisions", "verdict", "TEXT")]

# Side-effect class per tool. Unknown tools (including MCP tools) default to
# irreversible so rewind errs on the side of warning.
EFFECTS = {
    "Read": "pure", "Grep": "pure", "Glob": "pure", "LS": "pure",
    "WebSearch": "pure", "WebFetch": "pure", "TodoWrite": "pure",
    "Edit": "reversible", "MultiEdit": "reversible", "Write": "reversible",
    "NotebookEdit": "reversible",
    "apply_patch": "reversible",  # Codex file edits
}
# Tools whose input carries a shell command (Claude Code's Bash; Codex's shell tools).
SHELL_TOOLS = {"Bash", "shell", "exec_command", "local_shell"}


# Shell commands that only read. A Bash call is pure when every command in it is one of
# these and nothing is redirected to a file; anything else is irreversible.
READ_ONLY_COMMANDS = {
    "ls", "cat", "head", "tail", "less", "wc", "grep", "rg", "find", "fd", "tree", "pwd",
    "echo", "printf", "which", "file", "stat", "du", "df", "diff", "cut", "sort", "uniq",
    "awk", "jq", "date", "env", "whoami", "uname", "basename", "dirname", "realpath", "true",
}
READ_ONLY_GIT = {"status", "log", "diff", "show", "branch", "blame", "rev-parse", "ls-files",
                 "remote", "describe", "shortlog", "grep"}
READ_ONLY_TOOLS = {"riftguard": {"status", "log", "show", "decisions", "checkpoints"}}


def _bash_is_pure(command: str) -> bool:
    if re.search(r"(?<![0-9&])>|\$\(|`", command.replace("2>&1", "").replace(">/dev/null", "")):
        return False  # writes to a file, or runs a substitution we can't see into
    for segment in re.split(r"&&|\|\||;|\|", command):
        words = segment.split()
        while words and "=" in words[0]:  # VAR=value prefixes
            words = words[1:]
        if words[:2] == ["uv", "run"]:
            words = words[2:]
        if not words:
            continue
        cmd, sub = words[0], words[1] if len(words) > 1 else ""
        if cmd == "cd" or cmd in READ_ONLY_COMMANDS:
            continue
        if cmd == "git" and sub in READ_ONLY_GIT:
            continue
        if cmd in READ_ONLY_TOOLS and sub in READ_ONLY_TOOLS[cmd]:
            continue
        return False
    return True


def classify(tool: str, tool_input: Any) -> str:
    """Side-effect class of one tool call: pure, reversible or irreversible."""
    if tool in SHELL_TOOLS and isinstance(tool_input, dict):
        command = tool_input.get("command", "")
        if isinstance(command, list):  # Codex: ["bash", "-lc", "<script>"]
            command = command[-1] if command[:1] in (["bash"], ["sh"], ["zsh"]) else " ".join(command)
        return "pure" if _bash_is_pure(str(command)) else "irreversible"
    return EFFECTS.get(tool, "irreversible")


STEP_COLUMNS = "hash, agent_id, parent, tool, input_hash, output_hash, effect, ts"


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
    migrate(db)
    return db


def init_store(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = connect(path)
    db.executescript(SCHEMA)
    migrate(db)
    db.close()


def migrate(db: sqlite3.Connection) -> None:
    for table, column, kind in MIGRATIONS:
        cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if cols and column not in cols:  # empty: table not created yet
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


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
            (step, agent_id, parent, tool, inp, out, classify(tool, tool_input), ts),
        )
        db.execute("UPDATE agents SET head=? WHERE id=?", (step, agent_id))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return step


def get_step(db: sqlite3.Connection, prefix: str) -> Step:
    rows = db.execute(
        f"SELECT {STEP_COLUMNS} FROM steps WHERE hash LIKE ? LIMIT 2", (prefix + "%",)
    ).fetchall()
    if not rows:
        raise KeyError(f"no step matching {prefix}")
    if len(rows) > 1:
        raise KeyError(f"ambiguous step prefix {prefix}")
    return Step(*rows[0])


def walk(db: sqlite3.Connection, head: str | None) -> Iterator[Step]:
    """Yield steps from head back to the root, following parent pointers (like git log)."""
    while head:
        row = db.execute(f"SELECT {STEP_COLUMNS} FROM steps WHERE hash=?", (head,)).fetchone()
        if row is None:
            return
        step = Step(*row)
        yield step
        head = step.parent


def agent_for_worktree(db: sqlite3.Connection, worktree: str) -> tuple[str, str | None] | None:
    """(agent_id, head) of the most recently active agent in a worktree, if any.

    The MCP server has no session id, so this is how it learns who is calling. Two
    concurrent sessions in one worktree are ambiguous; riftguard expects one agent per worktree.
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


def auto_checkpoint(
    db: sqlite3.Connection, session_id: str, tool: str, tool_input: Any
) -> str | None:
    """Before an irreversible tool call, name the agent's current step so rewind has a safe
    point just before it. Returns the checkpoint name, or None if none was needed."""
    if classify(tool, tool_input) != "irreversible":
        return None
    row = db.execute("SELECT id, head FROM agents WHERE session_id=?", (session_id,)).fetchone()
    if row is None or row[1] is None:
        return None  # nothing recorded yet, so nothing to rewind to
    agent_id, head = row
    if db.execute("SELECT 1 FROM checkpoints WHERE step_hash=? AND name LIKE 'auto-%'",
                  (head,)).fetchone():
        return None  # already have one here
    detail = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    note = f"before {tool}: {detail}"[:200]
    return create_checkpoint(db, f"auto-{head[:10]}", agent_id, head, note)
