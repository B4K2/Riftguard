# AgentGit

Version control and coordination for AI coding agents. See `AgentGit Design and Build Plan.md`.

Phase 1 (recorder):

```bash
uv sync
uv run agentgit init        # inside a git repo: creates .agentgit/ and installs the Claude Code hook
uv run agentgit status
uv run agentgit log
uv run agentgit show <step-hash>
```
