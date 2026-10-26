"""Claude Code hook entry points. These must never break the agent: always exit 0."""

import json
import os
import sys
from typing import IO

from agentgit import store
from agentgit.judge import JUDGE_ENV  # set in the judge's own sessions, which we skip
from agentgit.paths import store_path, worktree_root


def post_tool_use(stdin: IO[str] = sys.stdin) -> None:
    if os.environ.get(JUDGE_ENV):
        return
    try:
        ev = json.load(stdin)
        path = store_path(ev["cwd"])
        if not path.exists():
            return  # repo not initialised with agentgit; stay silent
        db = store.connect(path)
        try:
            store.record_step(
                db,
                session_id=ev["session_id"],
                worktree=str(worktree_root(ev["cwd"])),
                tool=ev["tool_name"],
                tool_input=ev.get("tool_input"),
                tool_output=ev.get("tool_response"),
            )
        finally:
            db.close()
    except Exception as e:
        print(f"agentgit: {e}", file=sys.stderr)


def pre_tool_use(stdin: IO[str] = sys.stdin) -> None:
    """Checkpoint before irreversible calls. Never blocks: prints nothing to stdout."""
    if os.environ.get(JUDGE_ENV):
        return
    try:
        ev = json.load(stdin)
        path = store_path(ev["cwd"])
        if not path.exists():
            return
        db = store.connect(path)
        try:
            store.auto_checkpoint(db, ev["session_id"], ev["tool_name"], ev.get("tool_input"))
        finally:
            db.close()
    except Exception as e:
        print(f"agentgit: {e}", file=sys.stderr)
