---
id: 9
topic: rewind semantics
choice: History-only rewind: move agent head to checkpoint, log reason in rewinds table, never touch files
status: active
agent: gitagent-4971de5a
checkpoint: decision-rewind-semantics
date: 2026-10-26 22:20:37
---

# rewind semantics: History-only rewind: move agent head to checkpoint, log reason in rewinds table, never touch files

Agent CLIs already rewind file edits (Claude Code /rewind); riftguard adds shared history, the reason, and warnings about irreversible side effects
