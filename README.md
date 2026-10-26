# AgentGit

Version control and coordination for AI coding agents. AgentGit sits beside git: git versions
the code, AgentGit versions what agents do (every tool call) and decide (architectural choices),
so parallel agents on one repository don't silently contradict each other.

The core idea: if one agent has chosen Postgres, a second agent working in another worktree is
told before it introduces MongoDB, not at merge time.

## How it works

- **Hooks** record every Claude Code tool call into a content-addressed SQLite store
  (`.agentgit/local/store.db`). Each step points at its parent, like a git commit.
- **One shared store per repo.** Every git worktree resolves to the main repo's store, so
  agents see each other's decisions immediately.
- **An MCP server** gives agents `check_decisions`, `record_decision` and `checkpoint`. A section
  in `CLAUDE.md` tells them to check before making an architectural choice.
- **Decisions** are exported to `.agentgit/decisions/*.md` and committed; trajectories stay local.

## Install

```bash
uv tool install git+https://github.com/B4K2/GitAgent
# or: pipx install git+https://github.com/B4K2/GitAgent
```

Then, inside any git repository:

```bash
agentgit init
```

`init` creates `.agentgit/` and wires up whichever agent CLIs are installed
(`--harness claude|codex|both` to choose):

- **Claude Code:** hooks in `.claude/settings.json`, MCP server in `.mcp.json`, a section in
  `CLAUDE.md`. Restart Claude Code in the repo to pick them up.
- **Codex:** MCP server and hooks in `.codex/config.toml`, a section in `AGENTS.md`. Codex only
  loads project config for trusted projects, and asks you to approve new hooks (`/hooks`).

## Conflict judge

When an agent records a decision whose topic matches another agent's, a judge decides whether
they really conflict (Postgres vs MongoDB: yes; Postgres plus a Redis cache: no). A confirmed
conflict is held as **pending**, and the agent is told not to build on it until you settle it:

```bash
agentgit conflicts                 # pending decisions, the judge's verdict and rationale
agentgit resolve 4 --keep 3        # keep #3; #4 is rejected (or --keep 4 to replace #3)
```

The judge runs your own agent CLI headlessly (`claude -p` or `codex exec`) on your existing
login, so there is no API key or `.env` to set up. It runs in a fresh, tool-less session that
sees only the decisions, not the requesting agent's conversation, so the agent that wants the
change is not the one judging it. Configure it in `.agentgit/config.toml`:

```toml
[judge]
backend = "auto"   # auto (claude, else codex, else heuristic) | claude | codex | heuristic
model = ""         # e.g. "haiku"
```

`AGENTGIT_JUDGE_BACKEND` overrides the file. If the CLI fails, the keyword heuristic takes over
so agents are never blocked.

## Commands

| Command | What it does |
|---------|--------------|
| `agentgit status` | Agents, worktrees, step counts, last activity |
| `agentgit log [-a agent] [-t tool]` | Recorded steps, newest first |
| `agentgit show <hash>` | One step's input and output |
| `agentgit decisions [--all]` | Active (or all) decisions |
| `agentgit decide <topic> <choice> -r <reason>` | Record a decision by hand |
| `agentgit supersede <id>` | Retire a decision |
| `agentgit checkpoint <label>` | Name the current step of an agent |
| `agentgit checkpoints [--no-auto]` | List checkpoints, newest first |
| `agentgit conflicts` | Decisions held for review |
| `agentgit resolve <id> --keep <id>` | Settle a conflict |

Every tool call is tagged `pure`, `reversible` (file edits git can undo) or `irreversible`.
Read-only shell commands (`ls`, `grep`, `git status`, ...) count as pure. Before any irreversible
call, a PreToolUse hook creates an `auto-*` checkpoint so rewind always has a safe point just
before the step that can't be undone.

## Status

- [x] Phase 1: recorder, shared store, CLI
- [x] Phase 2: decision layer and MCP server
- [x] Automatic checkpoints before irreversible tool calls
- [x] Phase 3: judge (Claude Code / Codex / heuristic), pending decisions, `resolve`
- [x] Codex support (hooks, MCP, AGENTS.md)
- [ ] Judge evaluation on a labelled set of conflict / non-conflict pairs
- [ ] Phase 4: rewind and summary-seeded forks
- [ ] Phase 4: branch-and-compare (`fork --n 3`: parallel agents try different approaches from
      one checkpoint; tests or the judge pick a winner)
- [ ] Evaluation: measured steps and tokens saved by forks sharing a prefix, from the store

Current limitations: one agent per worktree; the judge only sees decisions whose topics match by
keyword/synonym, so differently-worded topics can slip past it; Bash classification is an
allowlist, so unknown commands count as irreversible; the Codex integration follows the Codex
docs and is tested against a stand-in binary, not a live Codex install yet.

## Related work

**AgentGit** (Li et al., 2025, [arXiv:2511.00628](https://arxiv.org/abs/2511.00628)) adds
Git-like commit, revert and branching to the state of a single LangGraph multi-agent workflow,
mainly so pipelines can roll back or explore prompt variants without recomputing shared steps.
This project instead coordinates *independent* coding agents (unmodified Claude Code sessions,
via hooks and MCP) working in parallel git worktrees: shared architectural decisions, conflict
warnings between agents, and explicit side-effect classes. Automatic checkpoints and
branch-and-compare are adapted from that paper.

## Development

```bash
uv sync
uv run pytest
uv run agentgit init   # in a dev venv, init pins this venv's interpreter path in the configs
```
