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

`init` creates `.agentgit/`, installs the PostToolUse hook in `.claude/settings.json`, registers
the MCP server in `.mcp.json`, and adds an AgentGit section to `CLAUDE.md`. Start (or restart)
Claude Code in the repo to pick them up.

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

## Status

- [x] Phase 1: recorder, shared store, CLI
- [x] Phase 2: decision layer and MCP server
- [ ] Phase 3: judge for semantic conflict detection and resolution
- [ ] Phase 4: rewind and summary-seeded forks

Current limitations: one agent per worktree; topic matching is a keyword/synonym heuristic until
the judge lands; all Bash calls are tagged irreversible.

## Development

```bash
uv sync
uv run pytest
uv run agentgit init   # in a dev venv, init pins this venv's interpreter path in the configs
```
