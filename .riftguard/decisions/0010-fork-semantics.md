---
id: 10
topic: fork semantics
choice: Fork = new unclaimed agent row whose head is the checkpoint step (shared history); seeded with a text summary; the forked session claims it via RIFTGUARD_FORK env on its first hook
status: active
agent: gitagent-4971de5a
checkpoint: decision-fork-semantics
date: 2026-10-26 22:38:01
---

# fork semantics: Fork = new unclaimed agent row whose head is the checkpoint step (shared history); seeded with a text summary; the forked session claims it via RIFTGUARD_FORK env on its first hook

Structural sharing costs nothing until divergence; env var is the only channel from launcher to hooks since the session id isn't known before launch
