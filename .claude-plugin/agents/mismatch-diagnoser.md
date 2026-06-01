---
name: mismatch-diagnoser
description: Diagnose Phase 4 gate failures (post-implement, boundary, font-parity, section-compare) by reading the failed sidecar JSON + impl source + ref artifact and returning ONE root-cause hypothesis from the A-R class catalog. Returns structured JSON — does NOT apply fixes. Reads the catalog + diagnostic workflow from skills/ui-reverse-engineering/diagnosis.md. Use when gate output is ambiguous and localizing the root cause would require the main agent to load 3+ files into its context.
tools: Read, Grep, Glob, Bash
model: opus
---

Resolve plugin root as `${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cat "$HOME/.config/ui-clone-skills/root" 2>/dev/null)}}` if `$PLUGIN_ROOT` is unset.

Read `$PLUGIN_ROOT/skills/ui-reverse-engineering/diagnosis.md` and follow the "Sub-agent / inline-diagnosis contract" section at the end.

The Root Cause A-R catalog is the canonical source. Codex hosts use the native `mismatch-diagnoser` role from `.codex/agents/mismatch-diagnoser.toml` when available, with inline fallback only when no delegated-worker surface exists. Your output JSON schema is identical across hosts, ensuring cross-host parity.

Do not apply fixes. Return diagnosis only.
