# idle-reset.sh — shared idle-state reset preamble for ground-truth extraction.
#
# SOURCE this file (do not exec it). It is the SINGLE definition of the
# idle-reset rule so the two ground-truth producers cannot drift:
#   - extract-section-map.sh  (writes section-map.json)
#   - extract-dom.sh          (writes structure.json)
# and it is also consumed by section-compare.sh to advisory-warn when the
# frozen-ref section-map.json it reuses was NOT captured in an idle state.
#
# WHY: rect/style ground truth is captured in whatever runtime state the page
# happens to be in. A stray hover bakes an open megamenu into section-map.json,
# which then OVERRIDES the frozen reference baseline the auto-research evaluator
# reuses — so a faithful idle clone permanently FAILs (navercorp A-06). The cure
# is to force the page back to a known idle state (scroll top + close hover/open
# states + rAF settle) BEFORE any rect/style read, assert nothing open-state
# remains, and record a `capturedIdle` provenance object on the artifact.
#
# Advisory-warn only: this NEVER mutates the page beyond the reset and NEVER
# aborts the caller. Residual open-state is surfaced on stderr + recorded in the
# provenance so a human/evaluator can see the ground truth may be contaminated.

# IDLE_RESET_JS — async IIFE run against an agent-browser session. Returns a
# JSON string {scrollY, openStateMatches, idle}. agent-browser eval supports
# awaited Promises (see hydration-check.sh), so the rAF settle happens in-page.
IDLE_RESET_JS='(async () => {
  // 1) scroll to the very top — most reveal/sticky state keys off scrollY.
  try { window.scrollTo(0, 0); } catch (_) {}
  // 2) close hover/focus-triggered open states (megamenus, dropdowns, tooltips)
  //    by dispatching leave events from every :hover element + body, and blur.
  try {
    const ev = (t) => new MouseEvent(t, { bubbles: true, cancelable: true, view: window });
    try {
      document.querySelectorAll(":hover").forEach((el) => {
        el.dispatchEvent(ev("mouseleave"));
        el.dispatchEvent(ev("mouseout"));
        try { el.dispatchEvent(new PointerEvent("pointerleave", { bubbles: true })); } catch (_) {}
      });
    } catch (_) {}
    if (document.body) document.body.dispatchEvent(ev("mouseleave"));
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  } catch (_) {}
  // 3) settle: two rAF ticks so scroll + close transitions commit to layout.
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  // 4) assert no open-state selector still matches. These are the conventional
  //    "this widget is open" hooks across frameworks; a match in an idle
  //    capture means a non-idle state is about to be baked into ground truth.
  const openSelectors = [
    "[aria-expanded=\"true\"]",
    "[data-state=\"open\"]",
    "[data-open=\"true\"]",
    "details[open]",
    "dialog[open]",
    ".is-open", ".is-active", ".menu-open", ".nav-open", ".is-expanded",
  ];
  const stillOpen = [];
  for (const sel of openSelectors) {
    try {
      const n = document.querySelectorAll(sel).length;
      if (n > 0) stillOpen.push({ selector: sel, count: n });
    } catch (_) {}
  }
  return JSON.stringify({
    scrollY: window.scrollY,
    openStateMatches: stillOpen,
    idle: window.scrollY === 0 && stillOpen.length === 0,
  });
})()'

# ab_idle_reset <session>
# Resets the page in <session> to idle ground truth, advisory-warns on residual
# open-state, and echoes a `capturedIdle` provenance JSON object to stdout. Safe
# under `set -euo pipefail`: always exits 0 (advisory only). Requires:
# agent-browser, python3. If the reset eval is unreachable/unparseable it still
# emits provenance with reset=false so the artifact records that idle was not
# confirmed.
ab_idle_reset() {
  local _session="$1" _raw _at
  _at="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")"
  _raw="$(agent-browser --session "$_session" eval "$IDLE_RESET_JS" 2>/dev/null || true)"
  printf '%s' "$_raw" | IDLE_RESET_AT="$_at" python3 -c '
import json, os, sys

raw = sys.stdin.read()
at = os.environ.get("IDLE_RESET_AT", "")
d = None
try:
    d = json.loads(raw)
    if isinstance(d, str):  # agent-browser double-encodes JSON.stringify returns
        d = json.loads(d)
except Exception:
    d = None

if isinstance(d, dict):
    prov = {
        "reset": True,
        "idle": bool(d.get("idle")),
        "scrollY": d.get("scrollY"),
        "openStateMatches": d.get("openStateMatches", []),
        "capturedAt": at,
        "helper": "idle-reset.sh",
    }
else:
    prov = {
        "reset": False,
        "idle": None,
        "scrollY": None,
        "openStateMatches": [],
        "capturedAt": at,
        "helper": "idle-reset.sh",
        "note": "reset-eval-unparseable",
    }

if not prov["idle"]:
    if not prov["reset"]:
        detail = "reset probe unreachable/unparseable"
    else:
        detail = "scrollY=%s openState=%s" % (prov["scrollY"], prov["openStateMatches"])
    sys.stderr.write(
        "idle-reset: ADVISORY — page not confirmed idle before capture (%s); "
        "ground-truth may bake a non-idle state. Proceeding.\n" % detail
    )

sys.stdout.write(json.dumps(prov))
' || printf '%s' '{"reset":false,"idle":null,"openStateMatches":[],"helper":"idle-reset.sh","note":"provenance-builder-failed"}'
}
