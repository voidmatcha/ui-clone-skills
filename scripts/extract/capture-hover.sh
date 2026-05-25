#!/usr/bin/env bash
# capture-hover.sh — Phase C hover-state snapshots
#
# Captures CSS `:hover` rule signal (declared properties from CSSOM) AND
# JS-handler hover signal (synthetic event-driven computed-style diff) in
# one in-page eval, so the impl can replicate hover transitions without
# guessing from bundle grep alone.
#
# Design: docs/multi-snapshot-capture-design.md § Phase C, with codex
# review (2026-05-25) applied:
#   [1] RISKY: synthetic dispatchEvent does NOT activate CSS :hover →
#       static CSSOM extraction is the CSS signal, no runtime trigger.
#   [2] RISKY: DOM-attribute hash misses paint-only hover changes →
#       computed-style hash over a fixed property set.
#   [3] OVER-ENG: 10×10 grid sweep dropped — agent-browser has no
#       pixel-coord cursor primitive. C1 deferred to a future C3 mode.
#   [4] RISKY: descendant `:hover` parsing — `.card:hover .title` means
#       activation=".card", affected=".card .title", NOT take-first-match.
#   [5] SAFE: manifest entries carry {id, kind, file, selector, ...} not
#       a selector-keyed map (selectors are ambiguous + non-durable).
#   [6] RISKY: CSS-hover signal is labeled separately from JS-hover; the
#       script never lets one satisfy the other.
#
# Usage:
#   capture-hover.sh <url> <session> <ref_dir> [--reuse-session]
#
# By default opens its own derived session `${session}-hover`. Pass
# `--reuse-session` to use the caller's session directly.
#
# Output:
#   <ref_dir>/states/hover/elem-<id>.json    — per-candidate snapshot
#   <ref_dir>/states/hover/manifest.json     — {entries: [{id,kind,file,selector,activation,changedCount,schemaVersion}]}
#   <ref_dir>/states/hover/summary.json      — {checked, durationMs, candidatesFound, candidatesProcessed,
#                                              candidatesCappedAt, candidatesWithCssRule, candidatesWithJsDiff,
#                                              candidatesWithAnySignal, schemaVersion}
#
# Exit codes:
#   0  capture completed (may be empty — no :hover rules + no JS handlers)
#   1  bad usage
#   2  agent-browser open failed
#   3  agent-browser eval returned unparseable / unexpected-shape response

set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <url> <session> <ref_dir> [--reuse-session]" >&2
  exit 1
fi

URL="$1"
SESSION="$2"
REF_DIR="$3"
REUSE_SESSION="false"
if [ "${4:-}" = "--reuse-session" ]; then
  REUSE_SESSION="true"
fi

HOVER_SESSION="${SESSION}-hover"
if [ "$REUSE_SESSION" = "true" ]; then
  HOVER_SESSION="$SESSION"
fi

OUTDIR="$REF_DIR/states/hover"
mkdir -p "$OUTDIR"

# Open page in the derived session unless reusing the caller's session.
if [ "$REUSE_SESSION" = "false" ]; then
  if ! agent-browser --session "$HOVER_SESSION" open "$URL" --wait 1500 >/dev/null 2>&1; then
    echo "capture-hover: agent-browser open failed for $URL (session=$HOVER_SESSION)" >&2
    exit 2
  fi
fi

# Single in-page eval — CSS rule extraction + JS-handler probing in one
# Promise loop. Total wall time ~50 × (200ms settle + 50ms restore) ≈ 12.5s
# worst-case, typically 2-5s for sites with <20 hover targets.
EVAL_JS='(async () => {
  const CAP = 50;
  const SETTLE_MS = 200;
  const RESTORE_MS = 50;
  const startedAt = performance.now();

  // 1. Parse CSSOM for :hover rules. Skip CORS-blocked sheets silently.
  const cssHoverRules = [];
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    if (!rules) continue;
    for (const rule of rules) {
      if (!rule.selectorText || !rule.style) continue;
      const sel = rule.selectorText;
      if (!sel.includes(":hover")) continue;
      // Split on top-level commas — avoid splitting inside :is() / :where() / :not().
      const parts = sel.split(/,(?![^()]*\))/);
      for (const part of parts) {
        const trimmed = part.trim();
        if (!trimmed.includes(":hover")) continue;
        // Codex [4]: activation is everything BEFORE :hover; affected is the
        // full selector with :hover removed. `.card:hover .title` →
        // activation=".card", affected=".card .title".
        const idx = trimmed.indexOf(":hover");
        const activation = trimmed.slice(0, idx).trim();
        const affected = trimmed.replace(":hover", "").trim();
        if (!activation) continue;
        const props = {};
        for (let i = 0; i < rule.style.length; i++) {
          const p = rule.style[i];
          props[p] = rule.style.getPropertyValue(p);
        }
        cssHoverRules.push({ activation, affected, cssProperties: props });
      }
    }
  }

  // 2. Deduplicate by activation+affected pair. Merge declared properties.
  const candidates = new Map();
  for (const r of cssHoverRules) {
    const key = r.activation + "|" + r.affected;
    if (candidates.has(key)) {
      Object.assign(candidates.get(key).cssProperties, r.cssProperties);
    } else {
      candidates.set(key, { activation: r.activation, affected: r.affected,
                            cssProperties: { ...r.cssProperties } });
    }
  }
  const candidatesFound = candidates.size;
  const orderedCandidates = Array.from(candidates.values()).slice(0, CAP);

  // Fixed property set for computed-style hash (codex [2]).
  const TRACKED = [
    "transform", "opacity", "color", "backgroundColor",
    "width", "height", "scale", "rotate", "translate",
    "filter", "boxShadow", "borderColor", "borderRadius",
  ];

  const elSelector = (el) => {
    const id = el.id ? "#" + el.id : "";
    const cls = (el.className && typeof el.className === "string")
      ? "." + el.className.trim().split(/\s+/).slice(0, 2)
              .filter(c => c).join(".") : "";
    return el.tagName.toLowerCase() + id + cls;
  };

  const csSnapshot = (el) => {
    const cs = getComputedStyle(el);
    const out = {};
    for (const p of TRACKED) out[p] = cs[p];
    return out;
  };

  // 3. For each candidate, JS-side hover probe: snapshot affected scope
  //    BEFORE, dispatch hover events, settle, snapshot AFTER, diff, restore.
  const results = [];
  for (const cand of orderedCandidates) {
    let activationEl = null;
    try { activationEl = document.querySelector(cand.activation); }
    catch (e) {}

    const result = {
      activation: cand.activation,
      affected: cand.affected,
      kind: "css",
      cssProperties: cand.cssProperties,
      jsChanges: [],
    };

    if (activationEl) {
      // Snapshot affected scope BEFORE (cap 10 elements per candidate).
      const observed = [];
      try {
        for (const el of document.querySelectorAll(cand.affected)) {
          observed.push(el);
          if (observed.length >= 10) break;
        }
      } catch (e) {}

      const before = new Map();
      for (const el of observed) before.set(el, csSnapshot(el));

      // Dispatch synthetic hover events. Catches JS-attached handlers
      // (GSAP, Framer Motion, vanilla listeners). Does NOT activate CSS
      // :hover — that signal is already captured from CSSOM above.
      const opts = { bubbles: true, cancelable: true, view: window };
      const optsNoBubble = { bubbles: false, cancelable: true, view: window };
      try {
        activationEl.dispatchEvent(new MouseEvent("mouseover", opts));
        activationEl.dispatchEvent(new MouseEvent("mouseenter", optsNoBubble));
        activationEl.dispatchEvent(new MouseEvent("mousemove", opts));
      } catch (e) {}

      await new Promise(r => requestAnimationFrame(() => setTimeout(r, SETTLE_MS)));

      // Snapshot AFTER + diff.
      for (const [el, b] of before) {
        if (!el.isConnected) continue;
        const a = csSnapshot(el);
        const changedProps = TRACKED.filter(k => a[k] !== b[k]);
        if (changedProps.length > 0) {
          result.jsChanges.push({
            selector: elSelector(el),
            computedStyleBefore: Object.fromEntries(changedProps.map(k => [k, b[k]])),
            computedStyleAfter: Object.fromEntries(changedProps.map(k => [k, a[k]])),
          });
        }
      }

      // Restore hover state.
      try {
        activationEl.dispatchEvent(new MouseEvent("mouseout", opts));
        activationEl.dispatchEvent(new MouseEvent("mouseleave", optsNoBubble));
      } catch (e) {}
      await new Promise(r => setTimeout(r, RESTORE_MS));

      if (result.jsChanges.length > 0) result.kind = "css+js";
    }

    results.push(result);
  }

  // Summary metrics — count CSS-rule signal and JS-diff signal separately
  // (codex [6]).
  let cssCount = 0, jsCount = 0, anySignal = 0;
  for (const r of results) {
    const hasCSS = r.kind === "css" || r.kind === "css+js";
    const hasJS = r.kind === "js" || r.kind === "css+js";
    if (hasCSS) cssCount++;
    if (hasJS) jsCount++;
    if (Object.keys(r.cssProperties).length > 0 || r.jsChanges.length > 0) anySignal++;
  }

  return {
    results,
    durationMs: Math.round(performance.now() - startedAt),
    candidatesFound,
    candidatesProcessed: orderedCandidates.length,
    candidatesCappedAt: CAP,
    candidatesWithCssRule: cssCount,
    candidatesWithJsDiff: jsCount,
    candidatesWithAnySignal: anySignal,
  };
})();'

RESPONSE_RAW="$(agent-browser --session "$HOVER_SESSION" eval --json "$EVAL_JS" 2>&1)" || {
  echo "capture-hover: agent-browser eval failed (session=$HOVER_SESSION)" >&2
  echo "$RESPONSE_RAW" >&2
  exit 3
}

# Validate + split into manifest / summary / per-elem files via python.
RESPONSE_TMP="$(mktemp -t capture-hover-resp.XXXX)"
printf '%s' "$RESPONSE_RAW" > "$RESPONSE_TMP"
trap 'rm -f "$RESPONSE_TMP"' EXIT
python3 - "$OUTDIR" "$RESPONSE_TMP" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

outdir = Path(sys.argv[1])
raw = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")

try:
    parsed = json.loads(raw)
except json.JSONDecodeError as e:
    print(f"capture-hover: invalid JSON from eval ({e}):\n{raw[:300]}", file=sys.stderr)
    sys.exit(3)

if isinstance(parsed, dict) and "result" in parsed and isinstance(parsed["result"], (dict, str)):
    inner = parsed["result"]
    if isinstance(inner, str):
        try:
            parsed = json.loads(inner)
        except json.JSONDecodeError:
            pass
    else:
        parsed = inner

if not isinstance(parsed, dict) or "results" not in parsed:
    print(f"capture-hover: unexpected payload shape:\n{json.dumps(parsed)[:300]}", file=sys.stderr)
    sys.exit(3)

results = parsed.get("results", [])

# Build manifest with codex [5] stable record shape:
# {id, kind, file, selector, activation, changedCount, schemaVersion}.
manifest_entries = []
seen_ids: set[str] = set()
for r in results:
    activation = r.get("activation", "")
    affected = r.get("affected", activation)
    kind = r.get("kind", "css")
    css_props = r.get("cssProperties", {}) or {}
    js_changes = r.get("jsChanges", []) or []
    if not css_props and not js_changes:
        # No CSS rule body + no JS diff → no signal worth recording.
        continue

    # Stable id: 8-char hex of "activation|affected|kind".
    raw_key = f"{activation}|{affected}|{kind}".encode("utf-8")
    entry_id = hashlib.sha256(raw_key).hexdigest()[:8]
    # Disambiguate the (rare) hash collision.
    if entry_id in seen_ids:
        suffix = 0
        while f"{entry_id}-{suffix}" in seen_ids:
            suffix += 1
        entry_id = f"{entry_id}-{suffix}"
    seen_ids.add(entry_id)

    fname = f"elem-{entry_id}.json"
    snap = {
        "id": entry_id,
        "activation": activation,
        "affected": affected,
        "kind": kind,
        "cssProperties": css_props,
        "jsChanges": js_changes,
        "schemaVersion": 1,
    }
    (outdir / fname).write_text(
        json.dumps(snap, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_entries.append({
        "id": entry_id,
        "kind": kind,
        "file": fname,
        "selector": activation,
        "activation": activation,
        "changedCount": len(js_changes),
        "schemaVersion": 1,
    })

(outdir / "manifest.json").write_text(
    json.dumps({"entries": manifest_entries}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

summary = {
    "checked": True,
    "durationMs": parsed.get("durationMs", 0),
    "candidatesFound": parsed.get("candidatesFound", 0),
    "candidatesProcessed": parsed.get("candidatesProcessed", 0),
    "candidatesCappedAt": parsed.get("candidatesCappedAt", 50),
    "candidatesWithCssRule": parsed.get("candidatesWithCssRule", 0),
    "candidatesWithJsDiff": parsed.get("candidatesWithJsDiff", 0),
    "candidatesWithAnySignal": parsed.get("candidatesWithAnySignal", 0),
    "schemaVersion": 1,
}
(outdir / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print(f"capture-hover: wrote {len(manifest_entries)} entry/entries to {outdir}/",
      file=sys.stderr)
PY
