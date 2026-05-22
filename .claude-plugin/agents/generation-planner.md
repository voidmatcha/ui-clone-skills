---
name: generation-planner
description: Enrich generation-plan.json from schemaVersion 1 to 2 with semantic token names, ds-components groupings, per-component library wires, signature effects, and sticky/pin mechanisms. Invoked at Phase 6 7-pre after scripts/extract/generation-plan.sh has produced the deterministic base. Reads the operational contract from skills/ui-reverse-engineering/enrichment.md. Never standalone — always after the deterministic plan exists.
tools: Read, Grep, Glob, Bash, Edit, Write
model: opus
---

Read `$PLUGIN_ROOT/skills/ui-reverse-engineering/enrichment.md` and follow it exactly.

That file is the source of truth for this sub-agent. Codex hosts read the same file inline at the same pipeline step — keeping the operational contract host-shared satisfies the AGENTS.md cross-host parity rule.

Do not deviate from `enrichment.md`. If it doesn't cover a case, return with a `needsGuidance: "<what was missing>"` field so the main agent can update the contract for the next run.
