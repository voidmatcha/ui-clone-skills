# Plugin Code Edits During Clone Iteration

`impl-scope-check.sh` exists to catch a real cheat pattern: an iteration
agent gets stuck on a clone, edits plugin gates or scripts, and then reports
the clone as done because the verifier became weaker. The default rule is
simple: a clone iteration edits only the implementation tree, `tmp/ref/**`,
and transient logs.

That rule must not block legitimate maintainer fixes. During hardening runs,
iteration agents can expose bugs in the plugin itself: shell quoting errors,
host output parsing bugs, stale-schema tolerance gaps, or a gate that
correctly identifies a class of issue but reads the wrong artifact. This
document defines the escalation path that separates a plugin fix from a
gate-cheat.

## Default Boundary

An iteration agent may edit:

- The resolved impl root, usually `impl/**` or the project-specific app
  directory.
- `tmp/ref/<component>/**` artifacts produced by capture and verification
  commands.
- Temporary logs under `tmp/**`.

An iteration agent must not edit:

- `skills/**`, `scripts/**`, `ui_clone/**`, `hooks/**`, `tests/**`.
- Plugin manifests, package metadata, CI scripts, or gate scripts.
- Thresholds, allowlists, or result artifacts just to clear the current
  clone.

If a blocked clone appears to require one of those edits, the iteration agent
stops and escalates. It does not make the plugin edit inline.

## Legitimate Plugin Fix Criteria

A plugin-code change is legitimate only when all of these are true:

1. The failure reproduces outside the current clone's visual mismatch. A
   minimal fixture, targeted test, or command output demonstrates the plugin
   bug.
2. The change improves a general rule, parser, host adapter, or documented
   policy. It is not a site-specific exception for the active reference.
3. The fix has its own regression test when executable behavior changes.
4. The fix can be reviewed independently from impl polishing work.
5. The clone iteration baseline is refreshed only after the plugin fix is
   committed or otherwise accepted by the maintainer.

Examples that qualify:

- `agent-browser` output changed from raw JSON to quoted-string JSON and a
  parser needs to tolerate both.
- A shell heredoc expands backticks inside an error message and crashes the
  scope checker.
- A new extraction artifact field is produced, but a downstream verifier does
  not consume it yet.
- A public escape hatch such as canvas replay needs an attested allowlist and
  a matching anti-cheat boundary.

Examples from the 2026-05-25 jsonl/review trail:

- `c2b027c` fixed quoted-string `agent-browser` eval output across runtime
  proof scripts. Legitimate: host output changed, multiple generic scripts
  needed the same parser tolerance.
- `07492bb` fixed `impl-scope-check.sh` heredoc quoting and passed
  `ALL_CHANGES` through the environment. Legitimate: the scope guard crashed
  before it could enforce policy.
- `110b51f` made `state-coverage` fail closed for motion-rich refs and
  stripped comments/generic Webflow classes from evidence. Legitimate:
  runtime state proof was accepting false evidence.
- `44cef90` made `verification-plan.sh` stale-aware of `states/*` and
  `animation-runtime-dump.json`. Legitimate: captures produced after the
  plan were not propagating into downstream motion gates.
- `0baef36` deepened GSAP runtime extraction with `CustomEase`, tween
  targets, and timeline children. Legitimate: downstream transition specs
  lacked runtime ground truth.
- `a27f786` made auto-verify understand structural-only mode. Legitimate:
  verifier orchestration was misreading a documented gate state.

Examples that do not qualify:

- Raising an AE threshold because the current section still differs.
- Adding the active site's domain to a blanket allowlist.
- Deleting a failing check or changing its status from `block` to `warn`
  without a general false-positive proof.
- Adding broad `STRUCTURAL_ONLY` coverage to avoid pixel polishing.

High-scrutiny examples:

- `ff48e17` treated self-declared skips as pass. This can be legitimate when
  it restores consistency with other gate surfaces, but it needs a companion
  audit for what evidence the skipped check no longer contributes.
- `83bb55e` tolerated a Phase 6d transition-coverage schema that had ref-side
  declarations but no samples. That is a schema-compatibility fix only when
  another runtime gate still proves the same motion family. Without that
  compensating proof, it is a fidelity relaxation and must not be hidden
  inside a clone iteration.

## Escalation Path

1. **Stop the iteration agent.** It returns a `plugin-fix-needed` or
   `contract-conflict` verdict with the failing command, artifact path,
   suspected plugin file, and the smallest reproduction it has.
2. **Preserve evidence.** Keep the failing `tmp/ref/<component>` artifacts,
   command output, and relevant session `jsonl` excerpt. Do not edit result
   files to make the current clone look clean.
3. **Switch to maintainer mode.** A main agent or maintainer investigates in
   the plugin repo, not inside the visual-debug iterator's scoped fix loop.
4. **Write the regression first.** For behavior changes, add a focused test
   that fails on the current plugin code. For docs-only policy changes, name
   the artifact or command path the policy governs.
5. **Make the plugin fix separately.** Keep it out of the impl-polishing
   diff. If public skill docs change, follow the release coupling rules:
   update `CHANGELOG.md` and plugin manifest versions together.
6. **Verify the plugin fix.** Run the targeted test and any relevant gate or
   script. For release-bound changes, run the repo's required CI commands.
7. **Refresh the iteration baseline.** After the plugin fix is accepted,
   delete the old baseline and reinitialize it from the new `HEAD`:

   ```bash
   rm tmp/ref/<component>/iteration-baseline-sha.txt
   bash skills/visual-debug/scripts/impl-scope-check.sh \
     tmp/ref/<component> <impl-root>
   ```

8. **Resume clone iteration.** Re-run the originally failing gate against the
   unchanged impl. If the gate now passes, continue normal polishing. If it
   still fails, the remaining work belongs to the impl iteration.

## Baseline Reset Rules

Deleting `iteration-baseline-sha.txt` is allowed only after a legitimate
plugin fix has landed. It is not a bypass for uncommitted edits. Before
resetting, check:

- `git status --short` shows no unexpected plugin changes.
- The plugin fix has a test or written policy justification.
- The relevant `jsonl` or review note shows why the plugin edit was needed
  during the run, not just that a plugin file changed.
- The active clone's impl files were not silently rewritten as part of the
  plugin fix.
- The next `impl-scope-check.sh` invocation writes `status=initialized` with
  the new baseline SHA.

If any of those checks fail, do not reset the baseline. Resolve the plugin
work first, then restart the clone iteration from a clean boundary.
