"""Seed summaries for forked sessions: the state at a checkpoint, not the full transcript."""

import re
import sqlite3
from typing import Any

from riftguard import decisions, store

EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)  # Codex apply_patch


def _detail(tool: str, inp: Any) -> str:
    """One-line gist of a tool call's input."""
    if not isinstance(inp, dict):
        return ""
    for key in ("file_path", "notebook_path", "command", "pattern", "query", "url", "label"):
        if key in inp:
            value = inp[key]
            return " ".join(value) if isinstance(value, list) else str(value)
    return ""


def _edited_files(tool: str, inp: Any) -> list[str]:
    if not isinstance(inp, dict):
        return []
    if tool in EDIT_TOOLS:
        path = inp.get("file_path") or inp.get("notebook_path")
        return [path] if path else []
    if tool == "apply_patch":
        return PATCH_FILE.findall(str(inp.get("input") or inp.get("patch") or inp))
    return []


def summary(db: sqlite3.Connection, checkpoint: str, why: str | None = None,
            recent: int = 15, max_files: int = 20) -> str:
    row = db.execute("SELECT step_hash, agent_id, note FROM checkpoints WHERE name=?",
                     (checkpoint,)).fetchone()
    if row is None:
        raise KeyError(f"no checkpoint {checkpoint}")
    step, source, note = row
    chain = list(store.walk(db, step))  # newest first
    edited: dict[str, None] = {}
    read: dict[str, None] = {}
    todos = None
    lines = []
    for s in chain:
        inp = store.get_object(db, s.input_hash)
        for path in _edited_files(s.tool, inp):
            edited.setdefault(path)
        if s.tool == "Read" and isinstance(inp, dict) and inp.get("file_path"):
            read.setdefault(inp["file_path"])
        if todos is None and s.tool == "TodoWrite" and isinstance(inp, dict):
            todos = inp.get("todos") or []
        if len(lines) < recent:
            lines.append(f"- {s.tool} [{s.effect}] {_detail(s.tool, inp)[:120]}")

    out = [f"You are a fork of agent {source}, starting from its checkpoint `{checkpoint}`"
           f"{f' ({note})' if note else ''}. This is a summary of its state at that point; "
           "you do not have its transcript."]
    if why:
        out += ["", f"Why this fork: {why}"]
    out += ["", "Files on disk may include work done after the checkpoint (by the source agent "
            "or others). Check `git status` and `git log` before relying on them."]
    active = decisions.active(db)
    if active:
        out += ["", "Active architectural decisions (follow these; use check_decisions and "
                "record_decision as usual):"]
        out += [f"- {d.summary()}" for d in active]
    if todos:
        out += ["", "Task list at the checkpoint:"]
        out += [f"- [{t.get('status', '?')}] {t.get('content', '')}" for t in todos
                if isinstance(t, dict)]
    if edited:
        out += ["", "Files it edited:"] + [f"- {p}" for p in list(edited)[:max_files]]
    reads = [p for p in read if p not in edited][:max_files]
    if reads:
        out += ["", "Files it read:"] + [f"- {p}" for p in reads]
    if lines:
        out += ["", f"Its last {len(lines)} steps, newest first:"] + lines
    return "\n".join(out) + "\n"
