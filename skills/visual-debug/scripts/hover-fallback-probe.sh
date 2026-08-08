#!/usr/bin/env bash
# hover-fallback-probe.sh — impl-side fallback probe for hover coverage.
#
# Loop-9 regression class: hover-state-compare's target cap selected five
# targets that all ended documented known-skips — 0 measured runs, and the
# gate passed. This probe gives every hoverable entry (spec hover entries +
# bundle-extraction hoverSizeExpansions like the nav pill label width
# expansion) a verdict the all-skip path can no longer dodge:
#   - pointer-event simulation on the live impl (JS-driven hover deltas:
#     width expansion, color change, ...)
#   - CSSOM scan for :hover rules covering the declared channels (CSS-driven
#     hover; synthetic events cannot activate :hover, and unmounted overlay
#     targets can only be verified this way)
# Verdicts live in ui_clone.gates.hover_probe.
#
# Usage: hover-fallback-probe.sh <session> <impl-url> <ref-dir>
#
# Env:
#   UI_CLONE_HOVER_PROBE_SAMPLES_FILE — pre-collected samples ({id: sample});
#                                       skips the browser (test fixtures).
#
# Writes: <ref-dir>/hover-fallback.json
# Exit: 0 pass/skip, 1 fail, 2 setup error

set -euo pipefail

SESSION="${1:?Usage: hover-fallback-probe.sh <session> <impl-url> <ref-dir>}"
IMPL_URL="${2:?Usage: hover-fallback-probe.sh <session> <impl-url> <ref-dir>}"
REF_DIR="${3:?Usage: hover-fallback-probe.sh <session> <impl-url> <ref-dir>}"

[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPTS_DIR/../../.." && pwd)"

# Provenance (batch-6 ITEM 4 / Attacks 3a/3b): a pass must rest on a REAL
# runtime scan. Set when this script actually drives agent-browser; env-injected
# samples (UI_CLONE_HOVER_PROBE_SAMPLES_FILE) leave it 0, so the verdict cannot
# grant coverage on fabricated/replayed samples or a forged measured-file.
RUNTIME_SCANNED=0
SAMPLES_FILE="${UI_CLONE_HOVER_PROBE_SAMPLES_FILE:-}"
if [ -z "$SAMPLES_FILE" ]; then
  command -v agent-browser >/dev/null 2>&1 || {
    echo "agent-browser not found in PATH" >&2
    exit 2
  }
  LIB_SRC="$(cat "$SCRIPTS_DIR/lib/visible-identity.js")"
  PLAN_JSON="$(PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m ui_clone.gates.hover_probe plan "$REF_DIR")"
  SAMPLES_FILE="$(mktemp "${TMPDIR:-/tmp}/hover-probe-samples.XXXXXX")"
  trap 'rm -f "$SAMPLES_FILE"' EXIT
  echo "{}" > "$SAMPLES_FILE"

  ENTRY_COUNT="$(printf '%s' "$PLAN_JSON" | python3 -c "import json,sys;print(len(json.load(sys.stdin)))")"
  if [ "$ENTRY_COUNT" -gt 0 ]; then
    agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1
    agent-browser --session "$SESSION" wait 2500 >/dev/null 2>&1
    RUNTIME_SCANNED=1
    for IDX in $(seq 0 $((ENTRY_COUNT - 1))); do
      ENTRY="$(printf '%s' "$PLAN_JSON" | python3 -c "import json,sys;print(json.dumps(json.load(sys.stdin)[$IDX]))")"
      EID="$(printf '%s' "$ENTRY" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")"
      # entries covered by a measured hover-state-compare run skip sampling
      # (the verdict marks them "measured" from the same file)
      if [ -n "${UI_CLONE_HOVER_MEASURED_FILE:-}" ] && [ -f "${UI_CLONE_HOVER_MEASURED_FILE:-}" ]; then
        IS_MEASURED="$(printf '%s' "$ENTRY" | python3 -c "
import json, sys
entry = json.load(sys.stdin)
measured = {l.strip() for l in open('$UI_CLONE_HOVER_MEASURED_FILE', encoding='utf-8') if l.strip()}
print('1' if any(s in measured for s in entry.get('selectors') or []) else '0')
" 2>/dev/null || echo 0)"
        if [ "$IS_MEASURED" = "1" ]; then
          echo "▸ $EID covered by measured run — skipping probe sampling"
          continue
        fi
      fi
      echo "▸ probing $EID"
      FIRST_SEL="$(printf '%s' "$ENTRY" | python3 -c "import json,sys;s=json.load(sys.stdin).get('selectors') or [''];print(s[0])")"
      # L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
      JS_FILE="$(mktemp "${TMPDIR:-/tmp}/hover-probe.XXXXXX")"
      mv "$JS_FILE" "${JS_FILE}.js"
      JS_FILE="${JS_FILE}.js"
      python3 - "$ENTRY" > "$JS_FILE" <<'PY'
import json
import sys

entry = json.loads(sys.argv[1])
selectors = json.dumps(entry.get("selectors") or [])

# NOTE: no backslash regexes — agent-browser eval applies one unescape pass;
# the visible-identity lib is prepended at eval time by the shell.
print(
    """(async () => {
  const sels = %s;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  // batch-11 ITEM 3: scroll-revealed hover targets (framer whileHover handlers on
  // elements unmounted/off-screen at scroll-top 0) are not reachable at idle.
  // Sweep the page first so IntersectionObserver/scroll-trigger MOUNTS them, then
  // the probe can resolve + drive them. Mirrors the masked-region probe sweep.
  const maxScroll = () => Math.max(document.documentElement.scrollHeight - window.innerHeight, 0);
  for (let i = 1; i <= 6; i++) {
    window.scrollTo({ top: (i / 6) * maxScroll(), behavior: "instant" });
    await wait(250);
  }
  // batch-13 ITEM 5: scroll-STATE-gated targets (a fixed nav translated
  // off-screen at scroll-top-0, mounted/revealed only past a ~150px threshold)
  // must be resolved with the page HELD at the reveal offset, not snapped back
  // to top where the nav re-hides and reports zero rendered height (the failing
  // size-expansion:label_container case). Scope the hold to nav/header/size
  // selectors so a below-fold miss on any other target is still surfaced (the
  // page returns to top as before for everything else).
  const __navGated = sels.some(s => /(^|[^a-z])(nav|header)|label[_-]?container/i.test(String(s || "")));
  window.scrollTo({ top: __navGated ? 220 : 0, behavior: "instant" });
  await wait(__navGated ? 400 : 200);
  // Resolve the VISIBLE target (reject display:none / off-screen / zero-area
  // decoys) so a superstring-class decoy cannot stand in for the real element.
  // batch-11 ITEM 3: scroll each candidate into view and accept below-fold (the
  // probe brought it through the viewport) so a faithful scroll-revealed target
  // resolves instead of false-failing "hover does not exist"; an unreachable
  // off-screen/zero-area decoy still fails isVisible.
  const findVisible = () => {
    for (const s of sels) {
      let cands = [];
      try { cands = Array.from(document.querySelectorAll(s)); } catch (e) { cands = []; }
      for (let i = 0; i < cands.length; i++) {
        try {
          var __r0 = cands[i].getBoundingClientRect();
          // batch-13 ITEM 5: only scroll an OFF-screen candidate into view. A
          // revealed in-view fixed/sticky nav must NOT be scrolled — scrollIntoView
          // on a top-anchored element snaps the page to top and re-hides the
          // scroll-state-gated nav, the exact reach failure this gate hits.
          if (__r0.top > window.innerHeight || __r0.bottom < 0) {
            cands[i].scrollIntoView({ block: "center", behavior: "instant" });
          }
        } catch (e) {}
        let recs = [];
        try { recs = __visibleIdentity.collect(s); } catch (e) { recs = []; }
        if (recs[i] && (
              __visibleIdentity.isVisible(recs[i], { requirePaint: false, belowFoldOk: true })
              // batch-12 ITEM 2: a probe-driven target displaced OFF the viewport
              // by a scroll-tied transform (a framer deck card with a large
              // translate, or a sticky-section card) is RENDERED + LAID OUT and can
              // be driven by synthetic/forced events even when isOnScreen rejects
              // it (the x-axis / above-viewport branches are not relaxed by
              // belowFoldOk). Resolve it on layout presence so the EXISTING event
              // dispatch reaches it; a display:none / zero-area / unmounted decoy
              // still fails isLaidOut, and a resolved target with no event delta
              // and no :hover rule still fails downstream (no minted coverage).
              || (__visibleIdentity.isRendered(recs[i]) && __visibleIdentity.isLaidOut(recs[i]))
              // batch-12 ITEM 6: a mounted-but-COLLAPSED size-expansion target (a
              // framer width:0->auto spring nav pill: display!=none, real height,
              // ~0 width) is rejected by isLaidOut (zero area), so the EXISTING
              // forced-CDP-hover size-proof never runs and the ref's own faithful
              // collapsed spring false-fails "unmounted". Resolve it so the forced
              // hover proves the width:0->auto expansion; a baked width:0 with NO
              // expansion is ALSO resolved but STAYS collapsed under forced hover
              // (_forced_grew False) -> still fails — the size proof, not static
              // layout, is the decoy reject; a display:none / zero-height / unmounted
              // decoy still fails this branch (display!=none + height>=2 required).
              || (function () {
                   var rc = recs[i].rect || recs[i];
                   var rw = (rc && typeof rc.width === "number") ? rc.width : 999;
                   var rh = (rc && typeof rc.height === "number") ? rc.height : 0;
                   return __visibleIdentity.isRendered(recs[i])
                     && String(recs[i].display || "").toLowerCase() !== "none"
                     && String(recs[i].visibility || "").toLowerCase() !== "hidden"
                     && (recs[i].opacity === undefined || parseFloat(recs[i].opacity) > 0)
                     && rh >= 2 && rw <= 1;
                 })()
            )) return cands[i];
      }
    }
    return null;
  };
  const el = findVisible();
  const tokenOf = (s) => {
    const t = s.replace("[class*=", "").split(String.fromCharCode(39)).join("")
      .split('"').join("").replace("]", "");
    return t.indexOf(".") === 0 ? t.substring(1) : t;
  };
  const tokens = sels.map(tokenOf).filter(Boolean);
  // Exact-target rule matching (Attack 2): a :hover rule only counts when its
  // base selector (sans :hover / & nesting) actually matches the resolved
  // target element or its lineage — NOT a mere substring of the token. A
  // display:none decoy whose class is a superstring no longer satisfies the
  // entry. Unmounted (el === null) falls back to token presence (can't match).
  const baseMatchesTarget = (full) => {
    if (!el) return false;
    const base = full.split(":hover").join(" ").split("&").join(" ").trim();
    if (!base) return false;
    let set = [];
    try { set = Array.from(document.querySelectorAll(base)); } catch (e) { return false; }
    return set.some(n => n === el || (n.contains && n.contains(el)) || (el.contains && el.contains(n)));
  };
  const cssProps = [];
  const walkRules = (rules, ancestry) => {
    for (const rule of Array.from(rules || [])) {
      const sel = rule.selectorText || "";
      const full = ancestry + " " + sel;
      const applies = el ? baseMatchesTarget(full) : tokens.some(t => full.indexOf(t) >= 0);
      if (rule.style && full.indexOf(":hover") >= 0 && applies) {
        for (let i = 0; i < rule.style.length; i++) {
          const propName = rule.style[i];
          cssProps.push(propName + ": " + rule.style.getPropertyValue(propName));
        }
      }
      if (rule.cssRules && rule.cssRules.length) {
        walkRules(rule.cssRules, full);
      }
    }
  };
  for (const sheet of Array.from(document.styleSheets)) {
    let rules = null;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    walkRules(rules, "");
  }
  if (!el) {
    return JSON.stringify({ found: false, cssHoverProps: cssProps });
  }
  el.scrollIntoView({block: "center"});
  await wait(500);
  const snap = () => {
    const r = el.getBoundingClientRect();
    const c = getComputedStyle(el);
    return { width: r.width, bg: c.backgroundColor, color: c.color,
             opacity: parseFloat(c.opacity), transform: c.transform };
  };
  const before = snap();
  const targets = [el];
  if (el.parentElement) targets.push(el.parentElement);
  if (el.parentElement && el.parentElement.parentElement) targets.push(el.parentElement.parentElement);
  const closest = el.closest("button, a, [role=button]");
  if (closest && targets.indexOf(closest) < 0) targets.push(closest);
  for (const t of targets) {
    ["pointerover", "pointerenter", "mouseover", "mouseenter", "mousemove"].forEach(type => {
      try { t.dispatchEvent(new MouseEvent(type, { bubbles: true })); } catch (e) {}
      try { t.dispatchEvent(new PointerEvent(type, { bubbles: true })); } catch (e) {}
    });
  }
  await wait(900);
  const after = snap();
  // batch-11 ITEM 3: unwind the JS whileHover state (leave/out events) so the
  // element returns to rest and a later entry's probe is not contaminated by a
  // stuck hover on this target.
  for (const t of targets) {
    ["pointerleave", "mouseleave", "pointerout", "mouseout"].forEach(type => {
      try { t.dispatchEvent(new MouseEvent(type, { bubbles: true })); } catch (e) {}
      try { t.dispatchEvent(new PointerEvent(type, { bubbles: true })); } catch (e) {}
    });
  }
  // batch-13 ITEM 5: report whether the hover TRIGGER is reachable. A floating
  // nav pill is clipped OFF-SCREEN (above the viewport) at every scroll position,
  // so a CDP hover cannot engage its framer whileHover spring; the verdict treats
  // non-expansion of an off-screen trigger as INCONCLUSIVE (an honest documented
  // skip), not proof of absence. An on-screen target that does not expand is
  // still provable absence -> fail.
  let __hov = el;
  const __anc = el.closest('button, a, [role="button"]');
  if (el.getBoundingClientRect().width < 2 && __anc) __hov = __anc;
  const __hr = __hov.getBoundingClientRect();
  const __off = (__hr.bottom <= 0 || __hr.top >= window.innerHeight
                 || __hr.right <= 0 || __hr.left >= window.innerWidth
                 || __hr.width <= 0 || __hr.height <= 0);
  return JSON.stringify({ found: true, cssHoverProps: cssProps, before: before, after: after, offScreen: __off });
})()""" % (selectors,)
)
PY
      # L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
      PROBE1="$(mktemp "${TMPDIR:-/tmp}/hover-probe-c.XXXXXX")"
      mv "$PROBE1" "${PROBE1}.js"
      PROBE1="${PROBE1}.js"
      { printf '%s;\n' "$LIB_SRC"; cat "$JS_FILE"; } > "$PROBE1"
      rm -f "$JS_FILE"
      RAW_FILE="$(mktemp "${TMPDIR:-/tmp}/hover-probe-raw.XXXXXX")"
      agent-browser --session "$SESSION" eval "$(cat "$PROBE1")" > "$RAW_FILE" 2>/dev/null || true
      rm -f "$PROBE1"

      # Forced (real CDP) hover: synthetic events cannot activate the native
      # :hover pseudo-class, so a `:hover{width:auto}` rule neutralized by a
      # higher-priority base rule would static-verify (Attack 1). Drive a REAL
      # hover and snapshot the COMPUTED end-state; the verdict uses it as the
      # authoritative size proof. Native :hover applies to ancestors of the
      # hovered element, so an ancestor-bound expansion still fires.
      FORCED_FILE="$(mktemp "${TMPDIR:-/tmp}/hover-forced-raw.XXXXXX")"
      echo "{}" > "$FORCED_FILE"
      if [ -n "$FIRST_SEL" ]; then
        # batch-13 ITEM 5: hold the page at the nav reveal offset so the forced
        # (CDP) hover reaches a scroll-state-gated nav pill hidden at scroll-top-0
        # (mirrors the synthetic probe's reveal hold above).
        REVEAL_Y="$(printf '%s' "$ENTRY" | python3 -c "
import json, re, sys
e = json.load(sys.stdin)
sels = e.get('selectors') or []
eid = str(e.get('id') or '')
nav = eid.startswith('size-expansion') or any(re.search(r'(^|[^a-z])(nav|header)|label[_-]?container', s or '', re.I) for s in sels)
print(220 if nav else 0)
" 2>/dev/null || echo 0)"
        if [ -n "$REVEAL_Y" ] && [ "$REVEAL_Y" != "0" ]; then
          agent-browser --session "$SESSION" eval "window.scrollTo({top:$REVEAL_Y,behavior:'instant'})" >/dev/null 2>&1 || true
          agent-browser --session "$SESSION" wait 400 >/dev/null 2>&1 || true
        fi
        agent-browser --session "$SESSION" hover "$FIRST_SEL" >/dev/null 2>&1 || true
        agent-browser --session "$SESSION" wait 700 >/dev/null 2>&1 || true
        # L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
        JS2_FILE="$(mktemp "${TMPDIR:-/tmp}/hover-forced.XXXXXX")"
        mv "$JS2_FILE" "${JS2_FILE}.js"
        JS2_FILE="${JS2_FILE}.js"
        python3 - "$ENTRY" > "$JS2_FILE" <<'PY'
import json
import sys

entry = json.loads(sys.argv[1])
selectors = json.dumps(entry.get("selectors") or [])
print(
    """(() => {
  const sels = %s;
  // batch-11 ITEM 3: same scroll-into-view + below-fold resolution as the
  // synthetic probe so the forced (CDP) hover end-state can also be measured for
  // a scroll-revealed size-expansion target.
  const findVisible = () => {
    for (const s of sels) {
      let cands = [];
      try { cands = Array.from(document.querySelectorAll(s)); } catch (e) { cands = []; }
      for (let i = 0; i < cands.length; i++) {
        try {
          var __r0 = cands[i].getBoundingClientRect();
          // batch-13 ITEM 5: only scroll an OFF-screen candidate into view. A
          // revealed in-view fixed/sticky nav must NOT be scrolled — scrollIntoView
          // on a top-anchored element snaps the page to top and re-hides the
          // scroll-state-gated nav, the exact reach failure this gate hits.
          if (__r0.top > window.innerHeight || __r0.bottom < 0) {
            cands[i].scrollIntoView({ block: "center", behavior: "instant" });
          }
        } catch (e) {}
        let recs = [];
        try { recs = __visibleIdentity.collect(s); } catch (e) { recs = []; }
        if (recs[i] && (
              __visibleIdentity.isVisible(recs[i], { requirePaint: false, belowFoldOk: true })
              // batch-12 ITEM 2: a probe-driven target displaced OFF the viewport
              // by a scroll-tied transform (a framer deck card with a large
              // translate, or a sticky-section card) is RENDERED + LAID OUT and can
              // be driven by synthetic/forced events even when isOnScreen rejects
              // it (the x-axis / above-viewport branches are not relaxed by
              // belowFoldOk). Resolve it on layout presence so the EXISTING event
              // dispatch reaches it; a display:none / zero-area / unmounted decoy
              // still fails isLaidOut, and a resolved target with no event delta
              // and no :hover rule still fails downstream (no minted coverage).
              || (__visibleIdentity.isRendered(recs[i]) && __visibleIdentity.isLaidOut(recs[i]))
              // batch-12 ITEM 6: a mounted-but-COLLAPSED size-expansion target (a
              // framer width:0->auto spring nav pill: display!=none, real height,
              // ~0 width) is rejected by isLaidOut (zero area), so the EXISTING
              // forced-CDP-hover size-proof never runs and the ref's own faithful
              // collapsed spring false-fails "unmounted". Resolve it so the forced
              // hover proves the width:0->auto expansion; a baked width:0 with NO
              // expansion is ALSO resolved but STAYS collapsed under forced hover
              // (_forced_grew False) -> still fails — the size proof, not static
              // layout, is the decoy reject; a display:none / zero-height / unmounted
              // decoy still fails this branch (display!=none + height>=2 required).
              || (function () {
                   var rc = recs[i].rect || recs[i];
                   var rw = (rc && typeof rc.width === "number") ? rc.width : 999;
                   var rh = (rc && typeof rc.height === "number") ? rc.height : 0;
                   return __visibleIdentity.isRendered(recs[i])
                     && String(recs[i].display || "").toLowerCase() !== "none"
                     && String(recs[i].visibility || "").toLowerCase() !== "hidden"
                     && (recs[i].opacity === undefined || parseFloat(recs[i].opacity) > 0)
                     && rh >= 2 && rw <= 1;
                 })()
            )) return cands[i];
      }
    }
    return null;
  };
  const el = findVisible();
  if (!el) return JSON.stringify({});
  const r = el.getBoundingClientRect();
  const c = getComputedStyle(el);
  return JSON.stringify({ width: r.width, bg: c.backgroundColor, color: c.color,
                          opacity: parseFloat(c.opacity), transform: c.transform });
})()""" % (selectors,)
)
PY
        # L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
        PROBE2="$(mktemp "${TMPDIR:-/tmp}/hover-forced-c.XXXXXX")"
        mv "$PROBE2" "${PROBE2}.js"
        PROBE2="${PROBE2}.js"
        { printf '%s;\n' "$LIB_SRC"; cat "$JS2_FILE"; } > "$PROBE2"
        rm -f "$JS2_FILE"
        agent-browser --session "$SESSION" eval "$(cat "$PROBE2")" > "$FORCED_FILE" 2>/dev/null || true
        rm -f "$PROBE2"
      fi

      python3 - "$SAMPLES_FILE" "$EID" "$RAW_FILE" "$FORCED_FILE" <<'PY' || true
import json
import sys


def _decode(path):
    try:
        value = open(path, encoding="utf-8", errors="replace").read().strip()
    except OSError:
        return None
    for _ in range(3):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return None
        else:
            break
    return value


samples_path, eid, raw_path, forced_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
value = _decode(raw_path)
if isinstance(value, dict):
    forced = _decode(forced_path)
    if isinstance(forced, dict) and forced:
        value["forcedHover"] = forced
    samples = json.loads(open(samples_path, encoding="utf-8").read())
    samples[eid] = value
    open(samples_path, "w", encoding="utf-8").write(json.dumps(samples))
PY
      rm -f "$RAW_FILE" "$FORCED_FILE"
    done
  fi
fi

# Provenance receipt (batch-7 ITEM 4b): when a live scan actually ran, drop a
# receipt INSIDE the impl tree. The verdict marks runtimeScanned=true only when
# the env flag AND this receipt file both exist, and the consumer binds the
# receipt to impl_root + mtime (PATH_CHECK) — so a self-attested env flag with
# fabricated samples (no browser) can no longer mint a pass.
SCAN_RECEIPT=""
if [ "${RUNTIME_SCANNED:-0}" = "1" ]; then
  IMPL_ROOT="$(bash "$REPO_ROOT/scripts/extract/find-impl-root.sh" "$REF_DIR" 2>/dev/null | head -1 || true)"
  # Anchor the receipt to the impl SOURCE tree when one exists. For
  # live-ref-as-impl runs (the ref-vs-ref-selfpass meta-check, where impl-url IS
  # the live reference and there is NO impl source tree) find-impl-root resolves
  # nothing — fall back to the ref-dir. The receipt only attests that a REAL
  # browser scan ran; RUNTIME_SCANNED is set solely AFTER an agent-browser open,
  # so a fabricated-samples run (no browser) never reaches here regardless of the
  # anchor. batch-13 ITEM 5: without this fallback hover-fallback can never
  # self-pass ref-vs-ref (provenance fails for want of an impl tree).
  RECEIPT_DIR=""
  if [ -n "${IMPL_ROOT:-}" ] && [ -d "$IMPL_ROOT" ]; then
    RECEIPT_DIR="$IMPL_ROOT"
  elif [ -d "$REF_DIR" ]; then
    RECEIPT_DIR="$REF_DIR"
  fi
  if [ -n "$RECEIPT_DIR" ]; then
    SCAN_RECEIPT="$RECEIPT_DIR/.hover-scan-receipt.json"
    python3 - "$SCAN_RECEIPT" "$RECEIPT_DIR" "$IMPL_URL" <<'PY' || true
import json
import sys
import time

receipt, impl_root, impl_url = sys.argv[1], sys.argv[2], sys.argv[3]
json_path = receipt
with open(json_path, "w", encoding="utf-8") as fh:
    json.dump({
        "schemaVersion": 1,
        "scannedAt": int(time.time()),
        "implRoot": impl_root,
        "implUrl": impl_url,
        "by": "hover-fallback-probe.sh",
    }, fh)
PY
  fi
fi

set +e
UI_CLONE_HOVER_RUNTIME_SCANNED="$RUNTIME_SCANNED" \
UI_CLONE_HOVER_SCAN_RECEIPT="$SCAN_RECEIPT" \
PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m ui_clone.gates.hover_probe verdict "$REF_DIR" "$SAMPLES_FILE"
CODE=$?
set -e

if [ -f "$REF_DIR/hover-fallback.json" ]; then
  python3 - "$REF_DIR/hover-fallback.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
cov = data.get("coverage") or {}
print(
    f"hover-fallback: status={data.get('status')} "
    f"verified={cov.get('verified')} static={cov.get('staticVerified')} "
    f"failed={cov.get('failed')}"
)
for entry in data.get("entries") or []:
    if entry.get("status") == "fail":
        print(f"  ✗ {entry.get('id')}: {entry.get('reason')}")
PY
fi

exit "$CODE"
