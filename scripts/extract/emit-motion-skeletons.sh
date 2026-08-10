#!/usr/bin/env bash
# emit-motion-skeletons.sh — deterministic motion-hook skeleton codegen.
#
# Why it matters:
#   transition-spec.json carries the real parameters of each signature motion:
#   the scroll-scrub property list + input range (ebay entry: width/height/
#   borderRadius over [0,.5,1], NOT scale), the scroll state-machine's state set
#   (initial/expanded/settled), and each carousel's EXACT Swiper config including
#   responsive breakpoints and mobile freeMode/scrollbar. Left to the LLM this is
#   re-authored or approximated: ebay's scrub was rewritten as scale(1->1.12) and
#   its state machine dropped with no trace; navercorp collapsed 4 extracted
#   swiper configs into one hardcoded config with matchMedia read once and zero
#   resize listeners. This script emits impl/src/generated/motion-skeletons.ts —
#   one `// spec:<id>`-tagged hook/init per entry with the parameters transcribed
#   verbatim — so the agent fills wiring TODOs only, never re-derives the params.
#
# Usage:
#   emit-motion-skeletons.sh <ref-dir> <impl-dir>
#
# Output:
#   <impl-dir>/src/generated/motion-skeletons.ts   (only when >=1 motion entry)
#   <ref-dir>/motion-skeletons-emitted.json        {emitted:[...], skipped:[...]}
#
# Exit: 0 always (a spec with no scrub/state/swiper entries is a valid no-op);
#       2 on setup error.

set -euo pipefail

REF_DIR="${1:?Usage: emit-motion-skeletons.sh <ref-dir> <impl-dir>}"
IMPL_DIR="${2:?Usage: emit-motion-skeletons.sh <ref-dir> <impl-dir>}"

SPEC="$REF_DIR/transition-spec.json"
if [ ! -f "$SPEC" ]; then
  echo "▸ emit-motion-skeletons: SKIP — no transition-spec.json in $REF_DIR"
  exit 0
fi
if [ ! -d "$IMPL_DIR" ]; then
  echo "ERROR: impl dir not found: $IMPL_DIR" >&2
  exit 2
fi

python3 - "$SPEC" "$IMPL_DIR" "$REF_DIR" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

spec_path = Path(sys.argv[1])
impl_dir = Path(sys.argv[2])
ref_dir = Path(sys.argv[3])

try:
    spec = json.loads(spec_path.read_text())
except (OSError, json.JSONDecodeError) as e:
    print(f"ERROR: cannot read {spec_path}: {e}", file=sys.stderr)
    sys.exit(2)

transitions = spec.get("transitions") if isinstance(spec, dict) else None
transitions = transitions if isinstance(transitions, list) else []

report = {"schemaVersion": 1, "module": "src/generated/motion-skeletons.ts",
          "emitted": [], "skipped": []}


def _pascal(s: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", s or "")
    return "".join(p[:1].upper() + p[1:] for p in parts if p) or "Motion"


def _classify(entry: dict) -> str:
    anim = entry.get("animation") if isinstance(entry.get("animation"), dict) else {}
    atype = str(anim.get("type") or entry.get("type") or "").lower()
    if "swiper" in atype or "carousel" in atype:
        return "swiper"
    if "state" in atype:
        return "state-machine"
    if "scrub" in atype or ("scroll" in atype and "driven" in atype) or "scroll-scrub" in atype:
        return "scroll-scrub"
    if "scroll" in atype:
        return "scroll-scrub"
    return ""


PROP_MAP = {
    "width": "width", "height": "height", "border-radius": "borderRadius",
    "borderradius": "borderRadius", "opacity": "opacity", "scale": "scale",
    "rotate": "rotate", "x": "x", "y": "y",
}


def _prop_key(raw: str):
    """(js-key or None, raw-token). None => non-standard, emit a TODO line."""
    token = raw.strip()
    base = token.split(":")[0].strip().lower()
    if base in PROP_MAP:
        return PROP_MAP[base], token
    if "translatex" in token.lower().replace(" ", "") or "translate3d" in token.lower() \
            or "translatex" in base or base == "transform" and "x" in token.lower():
        return "x", token
    if "translatey" in token.lower().replace(" ", ""):
        return "y", token
    camel = re.sub(r"-([a-z])", lambda m: m.group(1).upper(), base)
    if re.match(r"^[A-Za-z_$][\w$]*$", camel):
        return camel, token
    return None, token


def _parse_input(anim: dict):
    raw = anim.get("input")
    if isinstance(raw, list):
        vals = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            vals = json.loads(raw)
        except json.JSONDecodeError:
            vals = None
    else:
        vals = None
    if (
        isinstance(vals, list)
        and vals
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals)
        and all(0 <= v <= 1 for v in vals)
        and all(left <= right for left, right in zip(vals, vals[1:]))
    ):
        return vals, True
    return [0, 1], False


def _offset_from_branch(entry: dict):
    anim = entry.get("animation") if isinstance(entry.get("animation"), dict) else {}
    raw_offset = anim.get("offset")
    if isinstance(raw_offset, list) and all(isinstance(v, str) for v in raw_offset):
        return json.dumps(raw_offset)
    if isinstance(raw_offset, str) and raw_offset.strip().startswith("[") and raw_offset.strip().endswith("]"):
        return raw_offset.strip()
    bb = str(entry.get("bundle_branch") or "")
    m = re.search(r"offset\s*=\s*(\[[^\]]*\])", bb)
    return m.group(1) if m else None


def _norm_config(anim: dict) -> dict:
    cfg = {k: v for k, v in anim.items() if k not in ("type", "mobile")}
    if isinstance(cfg.get("pagination"), str):
        cfg["pagination"] = {"el": cfg["pagination"], "clickable": True}
    if isinstance(cfg.get("navigation"), str):
        cfg["navigation"] = {"nextEl": cfg["navigation"] + "-next",
                             "prevEl": cfg["navigation"] + "-prev"}
    return cfg


def _ts_obj(obj: dict, indent: int) -> str:
    """JSON is a valid TS object literal; re-indent to sit inside a function."""
    pad = " " * indent
    body = json.dumps(obj, indent=2)
    return "\n".join((pad + line) if i else line for i, line in enumerate(body.splitlines()))


scrubs, states, swipers = [], [], []
for e in transitions:
    if not isinstance(e, dict):
        continue
    kind = _classify(e)
    eid = str(e.get("id") or "").strip()
    if not kind:
        # F11: a motion entry whose type does not map to scrub/state/swiper
        # (e.g. "slider"/"slideshow", or an unknown type) is dropped from
        # codegen — but it must NOT vanish silently. Record it so the emitted
        # report accounts for every spec entry (only missing-id entries were
        # logged before).
        _anim = e.get("animation") if isinstance(e.get("animation"), dict) else {}
        _atype = str(_anim.get("type") or e.get("type") or "").strip()
        report["skipped"].append({
            "id": eid or None,
            "kind": None,
            "reason": (
                f"unclassified motion type {_atype!r} — not scroll-scrub / "
                "state-machine / swiper, so no skeleton is emitted"
            ),
        })
        continue
    if not eid:
        report["skipped"].append({"id": None, "kind": kind, "reason": "entry has no id"})
        continue
    (scrubs if kind == "scroll-scrub" else states if kind == "state-machine" else swipers).append(e)

need_scrub = bool(scrubs)
need_state = bool(states)
need_swiper = bool(swipers)

if not (need_scrub or need_state or need_swiper):
    (ref_dir / "motion-skeletons-emitted.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("▸ emit-motion-skeletons: no scroll/state/swiper entries — nothing emitted")
    sys.exit(0)

L: list[str] = []
L.append("// AUTO-GENERATED by scripts/extract/emit-motion-skeletons.sh — DO NOT EDIT the params.")
L.append("// One `// spec:<id>`-tagged hook/init per transition-spec motion entry, with the")
L.append("// scroll properties / input range / states / Swiper config transcribed VERBATIM from")
L.append("// the spec. Fill only the TODO wiring (refs, output stops, thresholds); do not")
L.append("// re-derive or approximate the parameters below.")
L.append("")

# ── imports (only what is used) ──────────────────────────────────────────────
react_named = (["useState"] if need_state else [])
react_types = (["RefObject"] if (need_scrub or need_state) else [])
framer_named = []
if need_scrub or need_state:
    framer_named.append("useScroll")
if need_scrub:
    framer_named.append("useTransform")
if need_state:
    framer_named.append("useMotionValueEvent")
if react_named:
    L.append(f"import {{ {', '.join(react_named)} }} from 'react';")
if react_types:
    L.append(f"import type {{ {', '.join(react_types)} }} from 'react';")
if framer_named:
    L.append(f"import {{ {', '.join(framer_named)} }} from 'framer-motion';")
if need_swiper:
    L.append("import Swiper from 'swiper';")
    L.append("import type { SwiperOptions } from 'swiper';")
L.append("")
L.append("const MOBILE_QUERY = '(max-width: 768px)';")

# ── scroll-scrub hooks ───────────────────────────────────────────────────────
for e in scrubs:
    eid = str(e.get("id"))
    pas = _pascal(eid)
    anim = e.get("animation") if isinstance(e.get("animation"), dict) else {}
    target = str(e.get("target") or e.get("selector") or "")
    prop_raw = str(anim.get("property") or "")
    props = [p for p in re.split(r"[,;]", prop_raw) if p.strip()]
    input_arr, input_exact = _parse_input(anim)
    input_lit = "[" + ", ".join(str(v) for v in input_arr) + "]"
    offset = _offset_from_branch(e) or "['start end', 'end start']"
    offset_todo = "" if _offset_from_branch(e) else "  // TODO: confirm useScroll offset from ref"
    L.append("")
    L.append(f"// spec:{eid} — {target}")
    L.append(f"// scroll-scrub | driver: {anim.get('driver', 'framer-motion useScroll')}")
    L.append(f"// property: {prop_raw or '(unspecified)'}  input: {input_lit}"
             + (f"  from: {anim.get('from')} -> to: {anim.get('to')}" if anim.get("from") or anim.get("to") else ""))
    if not input_exact:
        L.append("// TODO: input range not in spec — confirm the progress stops from the ref")
    L.append(f"export function use{pas}(targetRef: RefObject<HTMLElement>) {{")
    L.append(f"  const {{ scrollYProgress }} = useScroll({{ target: targetRef, offset: {offset} }});{offset_todo}")
    # Framer Motion's InputRange is a mutable number array. ``as const`` makes
    # this a readonly tuple and causes generated Next trees to fail typecheck.
    L.append(f"  const input = {input_lit};")
    returns = []
    for raw in props:
        key, tok = _prop_key(raw)
        if key is None:
            L.append(f"  // TODO: '{tok.strip()}' — non-standard property, wire manually")
            continue
        stops = ", ".join("/* TODO */ 0" for _ in input_arr)
        L.append(f"  // {tok.strip()} — {len(input_arr)} output stops matching input")
        L.append(f"  const {key} = useTransform(scrollYProgress, input, [{stops}]);")
        returns.append(key)
    if not returns and not props:
        L.append("  // TODO: spec has no property list — map the observed scrubbed properties here")
    L.append(f"  return {{ {', '.join(returns)} }};")
    L.append("}")
    report["emitted"].append({"id": eid, "kind": "scroll-scrub", "target": target,
                              "properties": props, "input": input_arr, "inputExact": input_exact})

# ── scroll state machines ────────────────────────────────────────────────────
for e in states:
    eid = str(e.get("id"))
    pas = _pascal(eid)
    anim = e.get("animation") if isinstance(e.get("animation"), dict) else {}
    target = str(e.get("target") or e.get("selector") or "")
    st = anim.get("states")
    st = [str(s) for s in st] if isinstance(st, list) and st else []
    if not st:
        report["skipped"].append({"id": eid, "kind": "state-machine",
                                  "reason": "animation.states missing/empty"})
        continue
    offset = _offset_from_branch(e) or "['start start', 'end end']"
    union = " | ".join(f"'{s}'" for s in st)
    L.append("")
    L.append(f"// spec:{eid} — {target}")
    L.append(f"// scroll-progress state machine | driver: {anim.get('driver', 'framer-motion useScroll')}")
    L.append(f"// property: {anim.get('property', '(unspecified)')}")
    L.append(f"export type {pas}State = {union};")
    L.append(f"export function use{pas}(targetRef: RefObject<HTMLElement>): {pas}State {{")
    L.append(f"  const {{ scrollYProgress }} = useScroll({{ target: targetRef, offset: {offset} }});")
    L.append(f"  const [state, setState] = useState<{pas}State>('{st[0]}');")
    L.append("  useMotionValueEvent(scrollYProgress, 'change', (p) => {")
    L.append(f"    // States (in order): {' -> '.join(st)}")
    L.append("    // TODO: fill the scrollYProgress thresholds (spec lists states, not numbers).")
    n = len(st)
    for i, s in enumerate(st[:-1]):
        thr = round((i + 1) / n, 3)
        kw = "if" if i == 0 else "else if"
        L.append(f"    {kw} (p < /* TODO threshold */ {thr}) setState('{s}');")
    L.append(f"    else setState('{st[-1]}');")
    L.append("  });")
    L.append("  return state;")
    L.append("}")
    report["emitted"].append({"id": eid, "kind": "state-machine", "target": target, "states": st})

# ── swiper carousels ─────────────────────────────────────────────────────────
for e in swipers:
    eid = str(e.get("id"))
    pas = _pascal(eid)
    anim = e.get("animation") if isinstance(e.get("animation"), dict) else {}
    target = str(e.get("target") or e.get("selector") or "")
    if not target.strip():
        report["skipped"].append({"id": eid, "kind": "swiper", "reason": "no target selector"})
        continue
    desktop = _norm_config(anim)
    mobile = anim.get("mobile") if isinstance(anim.get("mobile"), dict) else None
    has_bp = isinstance(desktop.get("breakpoints"), dict)
    L.append("")
    L.append(f"// spec:{eid} — {target}")
    if mobile:
        L.append("// Swiper rail with a mobile-specific config; rebuilt on breakpoint change")
        L.append("// (NOT one-shot — the ref rebuilds the instance when crossing the mobile query).")
    else:
        L.append("// Swiper carousel; responsive breakpoints handled by Swiper's own resize watch."
                 if has_bp else "// Swiper carousel.")
    L.append(f"export function init{pas}(): () => void {{")
    L.append(f"  const el = document.querySelector<HTMLElement>('{target}');")
    L.append("  if (!el) return () => {};")
    L.append(f"  const desktopConfig = {_ts_obj(desktop, 2)} as SwiperOptions;")
    if mobile:
        L.append(f"  const mobileConfig = {_ts_obj(mobile, 2)} as SwiperOptions;")
        L.append("  const mq = window.matchMedia(MOBILE_QUERY);")
        L.append("  let swiper: Swiper | null = null;")
        L.append("  const build = () => {")
        L.append("    if (swiper) { swiper.destroy(true, true); swiper = null; }")
        L.append("    swiper = new Swiper(el, mq.matches ? mobileConfig : desktopConfig);")
        L.append("  };")
        L.append("  build();")
        L.append("  mq.addEventListener('change', build);")
        L.append("  return () => { mq.removeEventListener('change', build); "
                 "if (swiper) swiper.destroy(true, true); };")
    else:
        L.append("  const swiper = new Swiper(el, desktopConfig);")
        L.append("  return () => swiper.destroy(true, true);")
    L.append("}")
    entry_rep = {"id": eid, "kind": "swiper", "target": target,
                 "hasBreakpoints": has_bp, "hasMobileConfig": bool(mobile)}
    report["emitted"].append(entry_rep)

(ref_dir / "motion-skeletons-emitted.json").write_text(
    json.dumps(report, indent=2) + "\n", encoding="utf-8")

if not report["emitted"]:
    print("▸ emit-motion-skeletons: no emittable entries after classification — nothing written")
    sys.exit(0)

gen_dir = impl_dir / "src" / "generated"
gen_dir.mkdir(parents=True, exist_ok=True)
out = gen_dir / "motion-skeletons.ts"
out.write_text("\n".join(L) + "\n", encoding="utf-8")

rel = os.path.relpath(out, impl_dir)
print(f"▸ emit-motion-skeletons: emitted {len(report['emitted'])} skeleton(s) -> {rel}")
for r in report["emitted"]:
    extra = ""
    if r["kind"] == "scroll-scrub":
        extra = f"props={r['properties']} input={r['input']}"
    elif r["kind"] == "state-machine":
        extra = f"states={r['states']}"
    elif r["kind"] == "swiper":
        extra = f"breakpoints={r['hasBreakpoints']} mobile={r['hasMobileConfig']}"
    print(f"    {r['id']:<32} [{r['kind']}] {extra}")
PY

exit 0
