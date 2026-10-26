<!-- riftguard:start -->
## Riftguard (multi-agent coordination)

This repo uses Riftguard. Other agents may be working in parallel worktrees.

- Before any architectural choice (datastore, framework, API style, auth, major dependency,
  project layout), call the `check_decisions` MCP tool with the topic. If a decision exists,
  follow it; do not introduce a competing choice.
- After making such a choice, call `record_decision(topic, choice, reason)`.
- If `record_decision` warns about a conflict, stop and align or ask the developer.
- Call `checkpoint(label)` before risky or exploratory work.
- Use these MCP tools for decisions; don't read files under `.riftguard/` directly.
<!-- riftguard:end -->
