# Conditional evidence contracts

Read only the section matching the evidence requested or the failed signal.

## Eval JSON

`agent-browser eval` already JSON-encodes the returned value. Return an object
directly when possible. If the script returns `JSON.stringify(obj)`, unwrap once
with `jq -r fromjson` before querying object fields. Write large results to a
file rather than stdout.

## Compact handoff

When a caller explicitly needs a downstream handoff, generate briefs from the
existing ref directory:

```bash
UV_PROJECT_ENVIRONMENT="${UI_CLONE_HOOK_VENV:-${XDG_CACHE_HOME:-$HOME/.cache}/ui-clone-skills/hook-venv}" \
  PYTHONPATH="$UI_CLONE_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  uv run --project "$UI_CLONE_ROOT" --no-dev --frozen python -m ui_clone.evidence_pack "$OUT_DIR" --out-dir "$OUT_DIR/brief"
```

This rolls up existing paths; it does not replace bundle analysis, transition
spec generation, or gate artifacts. Downstream work reads the briefs first and
opens raw evidence only by named path.

## Concrete element

For a target named by the user, mismatch report, or DOM evidence, use any valid
CSS selector; do not assume semantic classes:

```bash
TARGET_SELECTOR='<css-selector-from-dom-evidence>'
bash "$PLUGIN_ROOT/scripts/extract/element-evidence.sh" "$SESSION" "$URL" "$TARGET_SELECTOR" "$OUT_DIR/element-target.json"
```

This is an element probe that identifies a scoped clone's target; by itself it is
not proof that a section-only clone stayed within scope. Scoped fidelity still
needs the target's bundle, transition, state, and element-scope verification
evidence.

## Scroll state machine and mutations

When bundle or spec evidence contains `window.scrollTo`, `scrollYProgress`,
`setTimeout`, `velocity`, or a guard ref, capture
`initial → active/expanded → settled/returned`. Do not infer return behavior from
one endpoint.

Use canonical forward traversal and the measured post-readiness scroll range.
Reverse traversal is reset evidence. A height change alone does not prove
infinite scrolling. Failed capture invalidates old scroll snapshots and writes
`capture-scroll-error.json`; fix it before retrying. DOM mutation traces cover
transient class, ARIA/data, text, and child changes; continuous computed-style
motion remains trajectory evidence.

## Hover absence

Retire an inferred hover candidate only with the live bridge's
`absence-measured` receipt bound to the current CSS, DOM, and hover inputs.
Changed inputs require new measurement. Probe failure, an unreachable target, or
a selector never instantiated is unresolved evidence.

## Scroll replay

For an explicitly declared `scroll-driven` region, provide both
`artifacts.replayTrack` and `artifacts.replayTrackManifest`, or neither. Use
`scroll-progress` for scrubbed style or geometry and `scroll-action` only when a
single exact action starts pausable CSS/WAAPI motion. Timer/rAF replay needs two
fresh contexts with exact results. Ambiguous, velocity-dependent, and debounced
paths require state and settle evidence instead of guessed replay.

Lenis-owned position progress uses the explicit `lenis-wheel` transport and the
same calibrated `readyWaitMs` on reference and implementation. Do not relabel it
as native scrolling. Unsupported engines or markerless wheel interception remain
fail-closed until a truthful transport contract exists.
