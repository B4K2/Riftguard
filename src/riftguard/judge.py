"""Judges decide whether a new decision conflicts with existing ones.

Backends run the user's own agent CLI headlessly (Claude Code or Codex), so no API key or
.env is needed. Each judge call is a fresh session that sees only the decisions, not the
calling agent's conversation, so the agent that wants the change is not the one judging it.
"""

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from riftguard.decisions import Decision

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "conflict": {"type": "boolean"},
        "with_id": {"type": ["integer", "null"]},
        "severity": {"type": "string", "enum": ["none", "low", "high"]},
        "confidence": {"type": "number"},
        "recommendation": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["conflict", "with_id", "severity", "confidence", "recommendation", "rationale"],
    "additionalProperties": False,
}

PROMPT = """\
You are judging architectural decisions made by coding agents working in parallel on one
repository. Decide whether the NEW decision conflicts with any EXISTING decision: the two
cannot both hold without the system becoming incoherent (e.g. Postgres vs MongoDB as the
primary datastore). Choices that are compatible, complementary or about different concerns
(e.g. Postgres for data and Redis for caching) are not conflicts.

EXISTING decisions:
{existing}

NEW decision:
[{topic}] {choice} (reason: {reason})

Reply only with JSON matching the schema. with_id is the id of the conflicting existing
decision, or null. recommendation says which decision to keep and why, in one sentence.
confidence is between 0 and 1."""

# Set in the judge's environment so riftguard hooks ignore the judge's own session.
JUDGE_ENV = "RIFTGUARD_JUDGE"


@dataclass
class Verdict:
    conflict: bool
    with_id: int | None
    severity: str
    confidence: float
    recommendation: str
    rationale: str
    backend: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class Judge(Protocol):
    name: str

    def judge(self, topic: str, choice: str, reason: str | None,
              candidates: list[Decision]) -> Verdict: ...


class HeuristicJudge:
    """No model: any topic match with a different choice is a low-confidence conflict."""

    name = "heuristic"

    def judge(self, topic, choice, reason, candidates):
        for d in candidates:
            if d.choice.strip().lower() != choice.strip().lower():
                return Verdict(True, d.id, "high", 0.5, f"Keep #{d.id} ({d.choice}); it came first.",
                               "Same topic, different choice (heuristic, no model).", self.name)
        return Verdict(False, None, "none", 0.5, "No conflict.",
                       "Matching decisions make the same choice.", self.name)


class CLIJudge:
    """Runs an agent CLI headlessly, isolated from this repo's hooks, MCP and instructions."""

    name = "cli"
    binary = ""

    def __init__(self, model: str | None = None, timeout: float = 120):
        self.model = model
        self.timeout = timeout

    def command(self, workdir: Path) -> list[str]:
        raise NotImplementedError

    def read_output(self, proc: subprocess.CompletedProcess, workdir: Path) -> dict:
        raise NotImplementedError

    def judge(self, topic, choice, reason, candidates):
        prompt = PROMPT.format(
            existing="\n".join(d.summary() for d in candidates),
            topic=topic, choice=choice, reason=reason or "none given")
        # Run from an empty temp dir so no CLAUDE.md / AGENTS.md or project config is loaded.
        with tempfile.TemporaryDirectory(prefix="riftguard-judge-") as tmp:
            workdir = Path(tmp)
            (workdir / "schema.json").write_text(json.dumps(VERDICT_SCHEMA))
            proc = subprocess.run(
                self.command(workdir), input=prompt, cwd=workdir, capture_output=True,
                text=True, timeout=self.timeout, env={**os.environ, JUDGE_ENV: "1"})
            if proc.returncode != 0:
                raise RuntimeError(f"{self.binary} exited {proc.returncode}: {proc.stderr[-300:]}")
            data = self.read_output(proc, workdir)
        return Verdict(
            conflict=bool(data["conflict"]), with_id=data.get("with_id"),
            severity=data.get("severity", "high"), confidence=float(data.get("confidence", 0)),
            recommendation=str(data.get("recommendation", "")).strip(),
            rationale=str(data.get("rationale", "")).strip(),
            backend=self.name)


class ClaudeJudge(CLIJudge):
    name = "claude"
    binary = "claude"

    def command(self, workdir):
        cmd = ["claude", "-p", "--output-format", "json",
               "--json-schema", json.dumps(VERDICT_SCHEMA),
               "--tools", "", "--strict-mcp-config", "--setting-sources", "user",
               "--no-session-persistence", "--model", self.model or "haiku"]
        return cmd

    def read_output(self, proc, workdir):
        out = json.loads(proc.stdout)
        if isinstance(out.get("structured_output"), dict):
            return out["structured_output"]
        return _parse_json_text(out.get("result", ""))


class CodexJudge(CLIJudge):
    name = "codex"
    binary = "codex"

    def command(self, workdir):
        cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
               "-C", str(workdir), "--output-schema", str(workdir / "schema.json"),
               "-o", str(workdir / "verdict.json")]
        if self.model:
            cmd += ["-m", self.model]
        return cmd + ["-"]  # prompt on stdin

    def read_output(self, proc, workdir):
        return _parse_json_text((workdir / "verdict.json").read_text())


def _parse_json_text(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return json.loads(text)


BACKENDS = {"claude": ClaudeJudge, "codex": CodexJudge}


def get_judge(backend: str = "auto", model: str | None = None) -> Judge:
    """auto: Claude Code if installed, else Codex, else the heuristic."""
    if backend == "heuristic":
        return HeuristicJudge()
    if backend == "auto":
        for name, cls in BACKENDS.items():
            if shutil.which(cls.binary):
                return cls(model)
        return HeuristicJudge()
    if backend not in BACKENDS:
        raise ValueError(f"unknown judge backend {backend!r}")
    return BACKENDS[backend](model)


def judge_with_fallback(judge: Judge, topic, choice, reason, candidates) -> Verdict:
    """A broken or missing CLI must not block the agent: fall back to the heuristic."""
    try:
        return judge.judge(topic, choice, reason, candidates)
    except Exception as e:
        v = HeuristicJudge().judge(topic, choice, reason, candidates)
        v.rationale += f" ({judge.name} judge failed: {str(e)[:150]})"
        return v
