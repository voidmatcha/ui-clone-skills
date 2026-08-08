#!/usr/bin/env bash
# header-state-runtime-check.sh — prove that the impl header is a runtime
# state machine, not a static HTML paste.
#
# Usage:
#   header-state-runtime-check.sh <session> <ref-url> <impl-url> <ref-dir> [w] [h]
#
#
# What this gate does:
#   1. Probes the ref header at scroll=0 and scroll=600. If the nav root
#      (first `header` or `nav` element) does NOT mutate its className /
#      data-* attributes between the two states, the site has no stateful
#      header — gate writes status=skip and exits 0.
#   2. Probes the impl header the same way.
#   3. If ref mutated but impl did NOT, fails: the impl shipped static HTML
#      where the ref has a controller.
#   4. If both mutated, computes a fingerprint (set of toggled class names)
#      on each side and warns when ref toggles a class the impl never does
#      — typical signs: ref toggles is-hide / thema-* / track-animation /
#      js-scroll-animation but impl only toggles a single is-scrolled flag.
#
# Symmetric "no-controller" failure modes also caught:
#   - impl serializes the ref's settled-state HTML at scroll=200 verbatim,
#     so the impl rendering looks correct at scroll=200 but never changes
#     when the user scrolls. Test catches it because impl scroll=0 and
#     scroll=600 nav classNames are identical.
#   - impl loads the ref's raw runtime JS via <script src="...">. This is
#     a documented anti-pattern ("don't load ref-js to fake runtime"). The
#     gate doesn't ban it, but if the loaded JS doesn't actually attach
#     listeners (CORS / scope issues) the impl still fails the mutation
#     assertion.
#
# Writes:
#   <ref-dir>/header-state-runtime.json
#
# Exit 0 on pass/skip, 1 on missing mutation, 2 on setup error.

set -uo pipefail

# shellcheck source=../../../scripts/lib/viewport.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/scripts/lib/viewport.sh"

SESSION="${1:?Usage: header-state-runtime-check.sh <session> <ref-url> <impl-url> <ref-dir> [w] [h]}"
REF_URL="${2:?ref-url required}"
IMPL_URL="${3:?impl-url required}"
REF_DIR="${4:?ref-dir required}"
WIDTH="${5:-1440}"
HEIGHT="${6:-900}"

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "header-state-runtime: agent-browser CLI missing" >&2
  exit 2
fi
if [ ! -d "$REF_DIR" ]; then
  echo "header-state-runtime: ref-dir not found: $REF_DIR" >&2
  exit 2
fi

OUT="$REF_DIR/header-state-runtime.json"
REF_SESSION="${SESSION}-hdr-ref"
IMPL_SESSION="${SESSION}-hdr-impl"
PROBE_REF=$(mktemp -t hdr-ref.XXXX.json)
PROBE_IMPL=$(mktemp -t hdr-impl.XXXX.json)
trap 'rm -f "$PROBE_REF" "$PROBE_IMPL"; agent-browser --session "$REF_SESSION" close >/dev/null 2>&1 || true; agent-browser --session "$IMPL_SESSION" close >/dev/null 2>&1 || true' EXIT

# Probe script: collect className + data-* fingerprint at scroll=0 and
# scroll=600 from the first header/nav root encountered. Returns JSON
# with both fingerprints so the comparison happens in Python (avoids
# embedding string-diff logic in JS that has to survive shell quoting).
PROBE_JS='
(async () => {
  const findRoots = () => {
    const list = [];
    const header = document.querySelector("header") ||
                   document.querySelector("[role=banner]") ||
                   document.querySelector("nav");
    if (header) list.push({ name: "header", el: header });
    list.push({ name: "body", el: document.body });
    list.push({ name: "html", el: document.documentElement });
    const fwRootSelectors = ["#root", "#__next", "#__nuxt", "#app",
                             ".app-wrapper", ".layout-root", "[data-theme]"];
    for (const sel of fwRootSelectors) {
      const el = document.querySelector(sel);
      if (el && !list.find(x => x.el === el)) {
        list.push({ name: "fw-root:" + sel, el });
      }
    }
    return list;
  };
  const snap = (el) => {
    if (!el) return null;
    const dataAttrs = {};
    for (const a of el.attributes) {
      if (a.name === "class" || a.name.startsWith("data-")) {
        dataAttrs[a.name] = a.value;
      }
    }
    // Computed geometry trajectory: a header can be a state machine purely
    // via layout (height 100->64, padding shrink, transform translateY,
    // position fixed->absolute) WITHOUT toggling any class. Sample both
    // getComputedStyle (quantized props) and getBoundingClientRect (rect
    // height) on the SAME root so class + geometry verdicts describe one
    // element. scrollY is recorded so the Python side can skip the
    // geo-diff when scroll never advanced (Lenis / virtual scroll).
    let geo = null;
    try {
      const cs = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      geo = {
        height: Math.round((rect.height || 0) * 100) / 100,
        paddingTop: cs.paddingTop || "",
        paddingBottom: cs.paddingBottom || "",
        transform: cs.transform || "none",
        position: cs.position || "",
        top: cs.top || "",
        scrollY: Math.round(window.scrollY || window.pageYOffset || 0),
      };
    } catch (e) {
      geo = null;
    }
    return {
      tag: el.tagName.toLowerCase(),
      cls: el.className || "",
      attrs: dataAttrs,
      geo,
      childTagClasses: Array.from(el.querySelectorAll("*"))
        .slice(0, 40)
        .map(c => c.tagName.toLowerCase() + ":" + (c.className || "")),
    };
  };
  const snapAll = (roots) => roots.map(r => ({ name: r.name, snap: snap(r.el) }));
  const findRoot = () => {
    const roots = findRoots();
    return roots.length ? roots[0].el : null;
  };
  const root = findRoot();
  if (!root) return JSON.stringify({ found: false });
  window.scrollTo({ top: 0, behavior: "instant" });
  await new Promise(r => setTimeout(r, 400));
  const at0 = snap(findRoot());
  const allRoots0 = snapAll(findRoots());
  const sh = document.documentElement.scrollHeight || document.body.scrollHeight || 600;
  // Probes: 200px (early state change), proportional 25% (most sites),
  // 1200px (late-change parallax), and 50% of page height.
  const probes = [
    200,
    Math.min(600, Math.max(200, Math.floor(sh / 4))),
    Math.min(1200, sh - 100),
    Math.floor(sh / 2),
  ].filter((v, i, a) => v > 0 && a.indexOf(v) === i).sort((a, b) => a - b);
  const samples = [];
  let at600 = at0;
  for (const top of probes) {
    window.scrollTo({ top, behavior: "instant" });
    await new Promise(r => setTimeout(r, 350));
    const snapshot = snap(findRoot());
    samples.push({ top, snapshot });
    at600 = snapshot;  // last probe stays as the "deep" reference
  }
  const allRootsDeep = snapAll(findRoots());
  return JSON.stringify({
    found: true,
    at0, at600, samples,
    allRoots0,
    allRootsDeep,
    scrollHeight: sh,
  });
})()
'

agent_browser_href() {
  local session="$1"
  agent-browser --session "$session" eval '(() => location.href)()' 2>/dev/null | python3 -c '
import json
import sys

value = sys.stdin.read().strip()
for _ in range(5):
    if isinstance(value, dict):
        value = value.get("data") if value.get("data") is not None else value.get("result", "")
        continue
    if isinstance(value, str):
        try:
            value = json.loads(value)
            continue
        except Exception:
            break
    break
print(value if isinstance(value, str) else "")
' 2>/dev/null
}

canonical_http_href() {
  python3 -c '
import sys
from urllib.parse import urlsplit, urlunsplit

value = sys.stdin.read().strip()
try:
    parsed = urlsplit(value)
    port = parsed.port
except ValueError:
    raise SystemExit(1)
scheme = parsed.scheme.lower()
if scheme not in {"http", "https"} or not parsed.hostname:
    raise SystemExit(1)
host = parsed.hostname.lower()
if ":" in host:
    host = f"[{host}]"
default_port = 80 if scheme == "http" else 443
netloc = host if port in {None, default_port} else f"{host}:{port}"
path = parsed.path or "/"
print(urlunsplit((scheme, netloc, path, parsed.query, parsed.fragment)))
'
}

is_blank_browser_href() {
  local href="$1"
  case "$href" in
    about:blank|about:srcdoc|chrome-error://*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

probe_url() {
  local session="$1" url="$2" out_file="$3"
  if ! ab_open_at_viewport "$session" "$url" "$WIDTH" "$HEIGHT" 2; then
    echo "header-state-runtime-check: cannot probe at declared viewport ${WIDTH}x${HEIGHT}; failing closed" >&2
    exit 1
  fi
  local href
  if ! href="$(agent_browser_href "$session")"; then
    echo "header-state-runtime-check: location.href evaluation failed after viewport setup for $url" >&2
    exit 1
  fi
  if is_blank_browser_href "$href"; then
    # agent-browser 0.31.x can reset a just-opened page to about:blank while
    # applying viewport. Reopen after the viewport is established; otherwise
    # the JS probe sees body/html on a blank page and false-skips the ref.
    if ! agent-browser --session "$session" open "$url" >/dev/null 2>&1; then
      echo "header-state-runtime-check: viewport setup reset session to ${href:-empty href}, and reopen failed for $url" >&2
      exit 1
    fi
    sleep 2
    if ! href="$(agent_browser_href "$session")"; then
      echo "header-state-runtime-check: location.href evaluation failed after reopening $url" >&2
      exit 1
    fi
    if is_blank_browser_href "$href"; then
      echo "header-state-runtime-check: refusing to probe blank browser page after opening $url (href=${href:-empty})" >&2
      exit 1
    fi
  fi
  local canonical_href
  if ! canonical_href="$(printf '%s' "$href" | canonical_http_href)"; then
    echo "header-state-runtime-check: refusing to probe non-http(s) browser page after opening $url (href=${href:-empty})" >&2
    exit 1
  fi
  if ! agent-browser --session "$session" eval "$PROBE_JS" > "$out_file" 2>/dev/null; then
    echo "header-state-runtime-check: header probe evaluation failed at $canonical_href" >&2
    exit 1
  fi
}

probe_url "$REF_SESSION" "$REF_URL" "$PROBE_REF"
probe_url "$IMPL_SESSION" "$IMPL_URL" "$PROBE_IMPL"

python3 - "$OUT" "$PROBE_REF" "$PROBE_IMPL" "$REF_URL" "$IMPL_URL" <<'PY'
# Python 3.9 compat: union syntax via future-import.
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

out_path, ref_path, impl_path, ref_url, impl_url = sys.argv[1:6]

def read_probe(path: str) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return {
            "found": False,
            "measurementComplete": False,
            "error": "probe-missing",
        }
    # agent-browser eval emits the value as the last JSON-shaped line.
    # Scan from the end for the first line that parses as JSON.
    for line in reversed(text.strip().splitlines()):
        stripped = line.strip()
        if not stripped or not (stripped.startswith("{") or stripped.startswith("[") or stripped.startswith('"')):
            continue
        try:
            value = json.loads(stripped)
        except Exception:
            continue
        # eval prints the raw string return; loadAnimation/etc print
        # the JSON twice (once as JS string, once as the response).
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                continue
        if isinstance(value, dict) and isinstance(value.get("found"), bool):
            value["measurementComplete"] = True
            return value
    return {
        "found": False,
        "measurementComplete": False,
        "error": "probe-parse-failed",
    }

def signature(snap: dict | None) -> tuple[str, str, frozenset]:
    """Reduce a snap to (className, data-attrs-tuple, child-class-set)
    for set-comparison. Order-independent on attributes to avoid
    false positives from attribute reorder.
    """
    if not snap:
        return ("", "", frozenset())
    cls = snap.get("cls", "") or ""
    attrs = snap.get("attrs") or {}
    attrs_repr = "|".join(f"{k}={v}" for k, v in sorted(attrs.items()) if k != "class")
    child_set = frozenset(snap.get("childTagClasses") or [])
    return (cls, attrs_repr, child_set)

def mutates(at0: dict | None, at600: dict | None) -> bool:
    s0 = signature(at0)
    s6 = signature(at600)
    if s0 == s6:
        return False
    # Treat class-set delta as "real" mutation; pure inline-style change
    # without class toggle is allowed but doesn't count as a state
    # machine — the user's checklist names class toggles specifically.
    cls0 = set(re.split(r"\s+", s0[0]))
    cls6 = set(re.split(r"\s+", s6[0]))
    if cls0 ^ cls6:
        return True
    if s0[1] != s6[1]:
        return True
    if s0[2] != s6[2]:
        return True
    return False

ref_probe = read_probe(ref_path)
impl_probe = read_probe(impl_path)

reasons: list[str] = []
status = ""
ref_mutates = False
impl_mutates = False
ref_classes_toggled: set[str] = set()
impl_classes_toggled: set[str] = set()
ref_geo_changes = False
impl_geo_changes = False
ref_geo_samples: list = []
impl_geo_samples: list = []

def any_sample_mutates(probe: dict) -> tuple[bool, set]:
    """2026-05-22 universality audit: check mutation against ALL sample
    snapshots, not just the deepest one. Some sites flip class state
    earlier than our deepest probe; some flip later. Reporting
    mutation if ANY scroll point differs from scroll=0 catches both.
    Returns (mutated, classes_toggled_union).
    """
    at0 = probe.get("at0") or {}
    if not isinstance(at0, dict):
        return False, set()
    cls0 = set(re.split(r"\s+", at0.get("cls", "") or ""))
    samples = probe.get("samples") or []
    if not samples:
        # Fallback to legacy at600 comparison
        return mutates(at0, probe.get("at600")), set()
    union: set = set()
    for s in samples:
        snap = s.get("snapshot") or {}
        if not isinstance(snap, dict):
            continue
        if mutates(at0, snap):
            cls_n = set(re.split(r"\s+", snap.get("cls", "") or ""))
            union |= (cls0 ^ cls_n) - {""}
    return bool(union) or any(mutates(at0, s.get("snapshot")) for s in samples), union

def body_or_root_mutates(probe: dict) -> tuple[bool, list]:
    """2026-05-22 user request: state machine extends beyond <header>.
    Some sites toggle classes on document.body or document.documentElement
    (`<html>`) as scroll/theme state changes. Compare allRoots0 vs
    allRootsDeep; report mutation if body or html class delta is non-empty.
    Returns (mutated, list-of-root-names-with-mutation).
    """
    root0 = {r.get("name"): (r.get("snap") or {}) for r in (probe.get("allRoots0") or [])}
    root_deep = {r.get("name"): (r.get("snap") or {}) for r in (probe.get("allRootsDeep") or [])}
    mutated_roots: list[str] = []
    all_names = set()
    for r in (probe.get("allRoots0") or []) + (probe.get("allRootsDeep") or []):
        n = r.get("name")
        if n and n != "header":
            all_names.add(n)
    for name in sorted(all_names):
        s0 = root0.get(name) or {}
        sd = root_deep.get(name) or {}
        if not s0 or not sd:
            continue
        c0 = set(re.split(r"\s+", s0.get("cls", "") or ""))
        cd = set(re.split(r"\s+", sd.get("cls", "") or ""))
        if (c0 ^ cd) - {""}:
            mutated_roots.append(name)
    return bool(mutated_roots), mutated_roots

HEIGHT_THRESHOLD = 4.0  # px; sub-pixel/AA jitter wobbles <1px between settles

def geo_changes(probe: dict) -> tuple[bool, list]:
    """Detect a GEOMETRIC header state machine: does the header root move
    its computed geometry across the scroll probes? A class-less header
    that animates height/padding/transform/position/top on scroll is the
    real visible behaviour the class comparator is blind to.

    Returns (changed, samples) where samples is a list of
    [prop, scrollTop, from_value, to_value] for every property that moved
    relative to the scroll=0 baseline.

    Rules:
      - height: numeric, >= 4px delta to count (AA jitter guard).
      - paddingTop/paddingBottom/transform/position/top: exact string
        compare (these props are quantized → safe to compare verbatim).
      - Lenis / virtual-scroll guard: if scrollY never advanced past the
        baseline across any sample, return (False, []) so we don't flag an
        impl as "static" when the scroll itself never fired.
    """
    at0 = probe.get("at0") or {}
    base = (at0.get("geo") or {}) if isinstance(at0, dict) else {}
    if not base:
        return False, []
    samples = probe.get("samples") or []
    base_scroll = base.get("scrollY", 0)
    scroll_moved = False
    changed: list = []
    seen: set = set()
    str_props = ("paddingTop", "paddingBottom", "transform", "position", "top")
    for s in samples:
        snap = s.get("snapshot") or {}
        if not isinstance(snap, dict):
            continue
        geo = snap.get("geo") or {}
        if not geo:
            continue
        s_scroll = geo.get("scrollY", s.get("top", 0))
        if isinstance(s_scroll, (int, float)) and s_scroll > base_scroll:
            scroll_moved = True
        # height: numeric delta with threshold
        try:
            h0 = float(base.get("height"))
            hn = float(geo.get("height"))
            if abs(hn - h0) >= HEIGHT_THRESHOLD and ("height", h0, hn) not in seen:
                changed.append(["height", s_scroll, h0, hn])
                seen.add(("height", h0, hn))
        except (TypeError, ValueError):
            pass
        # quantized string props: exact compare
        for prop in str_props:
            v0 = base.get(prop)
            vn = geo.get(prop)
            if v0 is None or vn is None:
                continue
            if v0 != vn and (prop, v0, vn) not in seen:
                changed.append([prop, s_scroll, v0, vn])
                seen.add((prop, v0, vn))
    if not scroll_moved:
        # scroll never advanced (Lenis / virtual scroll didn't move) —
        # geometry diff is unreliable; do not flag either side.
        return False, []
    return bool(changed), changed

if not ref_probe.get("measurementComplete"):
    status = "fail"
    reasons.append(
        "ref header measurement failed "
        f"({ref_probe.get('error', 'unmeasured-reference')})"
    )
elif not impl_probe.get("measurementComplete"):
    status = "fail"
    reasons.append(
        "impl header measurement failed "
        f"({impl_probe.get('error', 'unmeasured-implementation')})"
    )
elif not ref_probe.get("found"):
    status = "skip"
    reasons.append(f"ref probe: no header/nav root found ({ref_probe.get('error','no-root')})")
else:
    ref_mutates, ref_classes_toggled = any_sample_mutates(ref_probe)
    ref_body_mutates, ref_body_roots = body_or_root_mutates(ref_probe)
    if ref_body_mutates and not ref_mutates:
        ref_mutates = True
        ref_classes_toggled = set()  # body/html names not header classes
    if ref_mutates and not ref_classes_toggled:
        # mutation present via attrs/childTagClasses, no class-only delta
        c0 = set(re.split(r"\s+", (ref_probe.get("at0") or {}).get("cls", "")))
        c6 = set(re.split(r"\s+", (ref_probe.get("at600") or {}).get("cls", "")))
        ref_classes_toggled = (c0 ^ c6) - {""}
    # Geometric state machine: a header can change height/padding/transform/
    # position on scroll with NO class toggle. Fold geometry into the ref's
    # "is this a state machine" verdict so class-less geometric headers stop
    # self-skipping (the realfood-gov 100->64 blind spot).
    ref_geo_changes, ref_geo_samples = geo_changes(ref_probe)

# A header is a verifiable state machine if it mutates class/attrs OR moves
# its geometry on scroll. Either makes impl parity mandatory.
ref_state = ref_mutates or ref_geo_changes

if status == "fail":
    pass
elif not ref_state:
    status = "skip"
    if ref_probe.get("found") and not reasons:
        reasons.append("ref header does not mutate or move on scroll — no state machine to verify")
else:
    if not impl_probe.get("found"):
        status = "fail"
        reasons.append("ref has a stateful header but impl has no header/nav root")
    else:
        impl_mutates, impl_classes_toggled = any_sample_mutates(impl_probe)
        impl_body_mutates, impl_body_roots = body_or_root_mutates(impl_probe)
        if impl_body_mutates and not impl_mutates:
            impl_mutates = True
            impl_classes_toggled = set()
        if impl_mutates and not impl_classes_toggled:
            c0 = set(re.split(r"\s+", (impl_probe.get("at0") or {}).get("cls", "")))
            c6 = set(re.split(r"\s+", (impl_probe.get("at600") or {}).get("cls", "")))
            impl_classes_toggled = (c0 ^ c6) - {""}
        impl_geo_changes, impl_geo_samples = geo_changes(impl_probe)

        if ref_geo_changes and not impl_geo_changes:
            # The suppressor case: ref header animates its geometry on
            # scroll (height/padding/transform/position) but impl header is
            # frozen — e.g. overrides.css pinned it with
            # position:absolute!important; transform:none!important. The
            # class comparator alone is blind to this; the geometry
            # trajectory catches it. This fails even when classes match.
            status = "fail"
            moved = ", ".join(sorted({s[0] for s in ref_geo_samples}))
            reasons.append(
                "ref header animates its geometry on scroll (" + moved + ") but the "
                "impl header geometry is frozen — impl is missing the runtime "
                "controller (likely a static header or an overrides.css "
                "position/transform suppressor)."
            )
        elif not impl_mutates and not impl_geo_changes:
            status = "fail"
            reasons.append(
                "ref header mutates className/data-* on scroll but impl header is static — "
                "impl is missing the runtime controller (likely shipped captured HTML)."
            )
        else:
            # Both are state machines. Warn (still pass) if ref toggles a
            # class family the impl never toggles — typical asymmetry: ref
            # does is-hide+thema-* while impl only does a single -scrolled.
            ref_only = ref_classes_toggled - impl_classes_toggled
            if ref_only:
                status = "pass"
                reasons.append(
                    "informational: ref toggles classes not seen in impl: "
                    + ", ".join(sorted(ref_only))
                )
            else:
                status = "pass"

payload = {
    "schemaVersion": 1,
    "status": status,
    "ref": {
        "url": ref_url,
        "measurementComplete": ref_probe.get("measurementComplete", False),
        "found": ref_probe.get("found", False),
        "mutates": ref_mutates,
        "classesToggled": sorted(ref_classes_toggled),
        "geoChanges": ref_geo_changes,
        "geoSamples": ref_geo_samples,
    },
    "impl": {
        "url": impl_url,
        "measurementComplete": impl_probe.get("measurementComplete", False),
        "found": impl_probe.get("found", False),
        "mutates": impl_mutates,
        "classesToggled": sorted(impl_classes_toggled),
        "geoChanges": impl_geo_changes,
        "geoSamples": impl_geo_samples,
    },
    "reasons": reasons,
    "nextAction": (
        "Implement a scroll-listener (or IntersectionObserver) that reproduces the "
        "ref header's runtime behaviour on scroll — toggling className "
        "(is-hide, thema-*, scroll classes, etc.) AND/OR animating its geometry "
        "(height, padding, transform, position) to match the ref trajectory. The "
        "ref header is a state machine; impl serializing one snapshot of the HTML "
        "(or pinning the header with an overrides.css position/transform suppressor) "
        "is a documented Tier 5 cheat. See the ref's compiled JS bundle for the exact "
        "class names, toggle conditions, and geometry keyframes."
        if (status == "fail") else "header state machine parity verified"
    ),
    "rule": (
        "If the ref header is a state machine on scroll — it mutates className/data-* "
        "attributes OR it animates its geometry (height, paddingTop/Bottom, transform, "
        "position, top) between scroll=0 and the deep probes — the impl header must "
        "reproduce that behaviour. A frozen impl geometry while the ref geometry moves "
        "proves the impl shipped a captured-HTML paste or an overrides.css "
        "position:absolute!important; transform:none!important suppressor with no "
        "runtime controller. When the ref header neither mutates nor moves, the gate skips."
    ),
}

Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": status, "reasons": reasons, "out": out_path}, ensure_ascii=False))
sys.exit({"pass": 0, "skip": 0, "fail": 1}.get(status, 2))
PY
