#!/usr/bin/env bash
# emit-scroll-helpers.sh — deterministic scroll codegen.
#
# Reads <ref-dir>/generation-plan.json and emits ready-to-use scaffold helpers
# into <impl-dir>/src/lib/ so the impl wires smooth scroll with the site's REAL
# parameters instead of hand-rolled defaults:
#
#   smoothScroll.required  → src/lib/SmoothScroll.tsx (Lenis raf loop, config
#                            from smoothScroll.config — Fix 28's threaded options)
#
# Idempotent: re-running overwrites the emitted file. No-op when the plan does
# not require smooth scroll. Existing hand-written helpers in other locations
# are left untouched (this only writes the canonical src/lib/ path).
#
# Usage: emit-scroll-helpers.sh <ref-dir> <impl-dir>
set -euo pipefail

REF_DIR="${1:?Usage: emit-scroll-helpers.sh <ref-dir> <impl-dir>}"
IMPL_DIR="${2:?Usage: emit-scroll-helpers.sh <ref-dir> <impl-dir>}"

PLAN="$REF_DIR/generation-plan.json"
if [ ! -f "$PLAN" ]; then
  echo "▸ emit-scroll-helpers: SKIP — no generation-plan.json in $REF_DIR"
  exit 0
fi

python3 - "$PLAN" "$IMPL_DIR" <<'PY'
import json
import sys
from pathlib import Path

plan_path = Path(sys.argv[1])
impl_dir = Path(sys.argv[2])

try:
    plan = json.loads(plan_path.read_text())
except (OSError, json.JSONDecodeError) as e:
    print(f"ERROR: cannot read {plan_path}: {e}", file=sys.stderr)
    sys.exit(2)

if not isinstance(plan, dict):
    print("▸ emit-scroll-helpers: malformed plan — nothing emitted")
    sys.exit(0)

lib = impl_dir / "src" / "lib"
emitted = []


def _emit(filename: str, body: str) -> None:
    lib.mkdir(parents=True, exist_ok=True)
    (lib / filename).write_text(body, encoding="utf-8")
    emitted.append(filename)


# ── SmoothScroll.tsx (Lenis, from smoothScroll.config — Fix 28) ──────────────
ss = plan.get("smoothScroll")
if isinstance(ss, dict) and ss.get("required"):
    # Only emit known Lenis options whose values are numeric or boolean — string
    # options (orientation/easing) would need quoting/function bodies we cannot
    # synthesize safely, and Lenis' defaults are correct for them.
    _NUMERIC_BOOL_KEYS = (
        "lerp", "duration", "wheelMultiplier", "touchMultiplier",
        "smoothWheel", "smoothTouch", "infinite", "syncTouch",
    )
    config = ss.get("config") if isinstance(ss.get("config"), dict) else {}
    opt_lines = []
    for k in _NUMERIC_BOOL_KEYS:
        if k not in config:
            continue
        v = config[k]
        if isinstance(v, bool) or isinstance(v, (int, float)):
            opt_lines.append(f"      {k}: {json.dumps(v)},")
    options_block = "\n".join(opt_lines)
    lenis_ctor = f"new Lenis({{\n{options_block}\n    }})" if options_block else "new Lenis()"
    _emit("SmoothScroll.tsx", f"""import {{ useEffect }} from "react";
import Lenis from "lenis";

/**
 * Deterministically emitted by skills/visual-debug/scripts/emit-scroll-helpers.sh
 * from generation-plan.json -> smoothScroll.config (the site's REAL Lenis
 * options). Lenis adds the `lenis` class to <html> and drives smooth scroll;
 * framer-motion useScroll tracks it because Lenis updates document scrollTop.
 * Do not hand-edit — re-run the emitter to refresh.
 */
export default function SmoothScroll({{ children }}: {{ children: React.ReactNode }}) {{
  useEffect(() => {{
    const lenis = {lenis_ctor};
    let raf = 0;
    const loop = (time: number) => {{
      lenis.raf(time);
      raf = requestAnimationFrame(loop);
    }};
    raf = requestAnimationFrame(loop);
    return () => {{
      cancelAnimationFrame(raf);
      lenis.destroy();
    }};
  }}, []);
  return <>{{children}}</>;
}}
""")


# ── ScrollReveal.tsx ─────────────────────────────────────────────────────────
# Fix 72 — when transition-spec.json grounds the reveal (an into-view Framer
# whileInView entry with real from/to/duration/ease extracted from the ref's
# bundle), emit a spec-parametrized whileInView ONE-SHOT: the ref's reveal is a
# timed entrance, not a scroll scrub, so the generic scrub (y 60, no
# ease/duration) produced a measurably different runtime trajectory
# (transition-fires / trajectory-compare). Falls back to the render-verified
# scrub pattern (Fix 29/31) when no spec entry exists.


def _spec_reveal_entry():
    spec_path = plan_path.parent / "transition-spec.json"
    try:
        spec = json.loads(spec_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for t in (spec.get("transitions") or []) if isinstance(spec, dict) else []:
        if not isinstance(t, dict):
            continue
        hint = " ".join(
            str(t.get(k) or "") for k in ("trigger", "bundle_branch", "id")
        ).lower()
        if "whileinview" not in hint and "into view" not in hint:
            continue
        anim = t.get("animation")
        if not isinstance(anim, dict):
            continue
        frm, to = anim.get("from"), anim.get("to")
        if not (isinstance(frm, dict) and isinstance(to, dict)):
            continue
        def _num(v, default):
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default
        ease = None
        try:
            parsed = json.loads(str(anim.get("ease") or ""))
            if isinstance(parsed, list) and len(parsed) == 4 and all(
                isinstance(x, (int, float)) for x in parsed
            ):
                ease = parsed
        except (json.JSONDecodeError, ValueError):
            ease = None
        return {
            "from_opacity": _num(frm.get("opacity"), 0),
            "from_y": _num(frm.get("y"), 80),
            "to_opacity": _num(to.get("opacity"), 1),
            "to_y": _num(to.get("y"), 0),
            "duration": _num(anim.get("duration"), 0.8),
            "ease": ease,
        }
    return None


sd = plan.get("scrollDriven")
_reveal = _spec_reveal_entry() if isinstance(sd, dict) and sd.get("required") else None
if _reveal is not None:
    ease_line = (
        f", ease: {json.dumps(_reveal['ease'])}" if _reveal["ease"] is not None else ""
    )
    _emit("ScrollReveal.tsx", f"""'use client';

import {{ motion }} from "framer-motion";

/**
 * Deterministically emitted by skills/visual-debug/scripts/emit-scroll-helpers.sh
 * from transition-spec.json — the ref's REAL into-view reveal (Framer
 * whileInView one-shot): from/to/duration/ease are extracted from the ref
 * bundle, not invented. Wrap reveal sections in <ScrollReveal>.
 * Do not hand-edit — re-run the emitter to refresh.
 */
export default function ScrollReveal({{
  children,
  className,
}}: {{
  children: React.ReactNode;
  className?: string;
}}) {{
  return (
    <motion.div
      className={{className}}
      data-scroll-reveal="1"
      initial={{{{ opacity: {json.dumps(_reveal['from_opacity'])}, y: {json.dumps(_reveal['from_y'])} }}}}
      whileInView={{{{ opacity: {json.dumps(_reveal['to_opacity'])}, y: {json.dumps(_reveal['to_y'])} }}}}
      viewport={{{{ once: true }}}}
      transition={{{{ duration: {json.dumps(_reveal['duration'])}{ease_line} }}}}
    >
      {{children}}
    </motion.div>
  );
}}
""")
elif isinstance(sd, dict) and sd.get("required"):
    _emit("ScrollReveal.tsx", """'use client';

import { useRef } from "react";
import { motion, useScroll, useTransform } from "framer-motion";

/**
 * Deterministically emitted by skills/visual-debug/scripts/emit-scroll-helpers.sh
 * from generation-plan.json -> scrollDriven (Framer useScroll/useTransform).
 * Render-verified pattern: maps the element's own scroll progress onto opacity
 * + translateY. Wrap reveal sections in <ScrollReveal>. Tracks Lenis-driven
 * scroll automatically (Lenis updates document scrollTop, which useScroll
 * observes). Do not hand-edit — re-run the emitter to refresh.
 */
export default function ScrollReveal({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ["start end", "start center"],
  });
  const opacity = useTransform(scrollYProgress, [0, 1], [0, 1]);
  const y = useTransform(scrollYProgress, [0, 1], [60, 0]);
  return (
    <motion.div ref={ref} className={className} data-scroll-reveal="1" style={{ opacity, y }}>
      {children}
    </motion.div>
  );
}
""")


# ── ScrollStateDriver.tsx (Fix 74) ───────────────────────────────────────────
# The ref's scroll-position-state fade (e.g. animate:{opacity: a?1:.5}) is
# JS-driven — no CSS transition marker — so captured elements freeze at the
# INACTIVE opacity and the clone produces no runtime delta when the trigger is
# driven (transition-fires). The transpiler stamps such frozen-at-from-state
# elements with data-scroll-fade; this driver animates them to the active
# state with the spec's real duration/ease via WAAPI (fill:forwards), so the
# computed-style delta is measurable.


def _spec_state_fade_entry():
    spec_path = plan_path.parent / "transition-spec.json"
    try:
        spec = json.loads(spec_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for t in (spec.get("transitions") or []) if isinstance(spec, dict) else []:
        if not isinstance(t, dict):
            continue
        hint = " ".join(
            str(t.get(k) or "") for k in ("trigger", "bundle_branch", "id")
        ).lower()
        if "scroll" not in hint or "state" not in hint:
            continue
        anim = t.get("animation")
        if not isinstance(anim, dict):
            continue
        frm, to = anim.get("from"), anim.get("to")
        if not (isinstance(frm, dict) and isinstance(to, dict)):
            continue
        fo, to_o = frm.get("opacity"), to.get("opacity")
        if not (
            isinstance(fo, (int, float)) and not isinstance(fo, bool)
            and isinstance(to_o, (int, float)) and not isinstance(to_o, bool)
            and 0 < fo < 1
        ):
            continue
        dur = anim.get("duration")
        dur = dur if isinstance(dur, (int, float)) and not isinstance(dur, bool) else 0.8
        easing = "ease"
        try:
            parsed = json.loads(str(anim.get("ease") or ""))
            if isinstance(parsed, list) and len(parsed) == 4 and all(
                isinstance(x, (int, float)) for x in parsed
            ):
                easing = "cubic-bezier(" + ", ".join(str(x) for x in parsed) + ")"
        except (json.JSONDecodeError, ValueError):
            pass
        return {"from_opacity": fo, "to_opacity": to_o, "ms": int(round(dur * 1000)), "easing": easing}
    return None


# ── stroke-draw entry (Fix 76) — strokeDashoffset draw-in for SVG paths ─────


def _spec_stroke_draw_entry():
    spec_path = plan_path.parent / "transition-spec.json"
    try:
        spec = json.loads(spec_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for t in (spec.get("transitions") or []) if isinstance(spec, dict) else []:
        if not isinstance(t, dict):
            continue
        hint = " ".join(
            str(t.get(k) or "") for k in ("trigger", "bundle_branch", "id")
        ).lower()
        anim = t.get("animation")
        prop = str(anim.get("property") or "").lower() if isinstance(anim, dict) else ""
        if "strokedashoffset" not in hint.replace("-", "") and "strokedashoffset" not in prop.replace("-", ""):
            continue
        dur = anim.get("duration") if isinstance(anim, dict) else None
        dur = dur if isinstance(dur, (int, float)) and not isinstance(dur, bool) else 1.0
        easing = "ease"
        try:
            parsed = json.loads(str(anim.get("ease") or "")) if isinstance(anim, dict) else None
            if isinstance(parsed, list) and len(parsed) == 4 and all(
                isinstance(x, (int, float)) for x in parsed
            ):
                easing = "cubic-bezier(" + ", ".join(str(x) for x in parsed) + ")"
        except (json.JSONDecodeError, ValueError):
            pass
        return {"ms": int(round(dur * 1000)), "easing": easing}
    return None


_state = _spec_state_fade_entry() if isinstance(sd, dict) and sd.get("required") else None
_stroke = _spec_stroke_draw_entry() if isinstance(sd, dict) and sd.get("required") else None
# Coherence guard (Fix 115): the transpiler stamps data-stroke-draw via a
# marker-less heuristic (Fix 114 — fully-hidden stroke-dashoffset) and
# data-scroll-fade even when transition-spec.json missed the animation, and the
# emitted entry mounts <ScrollStateDriver /> whenever ANY stamp exists. If the
# driver is emitted only when the spec carries the animation, those heuristic
# stamps mount a driver that was never written → broken import / inert draw-in.
# Key the emit on the ACTUAL stamps in the generated components so mount ⟺ emit
# always holds, falling back to a generic duration/ease when the spec lacks one.
# rglob the whole src tree (not just src/components): scaffold-to-jsx's --out-dir
# override can place components elsewhere, and the stamps only live in component
# JSX (the entry/lib carry none), so a broad scan is both robust and safe.
_src_dir = impl_dir / "src"
_comp_blob = "".join(
    p.read_text(encoding="utf-8") for p in _src_dir.rglob("*.tsx")
) if _src_dir.is_dir() else ""
if _stroke is None and "data-stroke-draw" in _comp_blob:
    _stroke = {"ms": 800, "easing": "ease"}
if _state is None and "data-scroll-fade" in _comp_blob:
    _state = {"from_opacity": 0.0, "to_opacity": 1.0, "ms": 800, "easing": "ease"}
_stroke_block = ""
if _stroke is not None:
    # A draw-in path often lives inside <mask>/<defs> (checkmark masks) — it has
    # NO render box, so IntersectionObserver can never fire on the path itself.
    # Observe the element that REFERENCES the mask (mask="url(#id)") or the
    # nearest boxed svg ancestor, and animate the path when THAT enters view.
    _stroke_block = f"""
    const strokes = Array.from(document.querySelectorAll("[data-stroke-draw]")) as SVGPathElement[];
    const strokeFor = new Map<Element, SVGPathElement[]>();
    for (const p of strokes) {{
      let target: Element = p;
      const holder = p.closest("mask, defs");
      const hid = holder ? holder.id : "";
      if (hid) {{
        const ref = document.querySelector('[mask*="#' + hid + '"], [style*="#' + hid + '"]');
        if (ref) target = ref;
      }}
      if (target === p) {{
        const svg = p.closest("svg");
        if (svg) {{
          const r = svg.getBoundingClientRect();
          if (r.width > 0 || r.height > 0) target = svg;
        }}
      }}
      const list = strokeFor.get(target) || [];
      list.push(p);
      strokeFor.set(target, list);
    }}
    const strokeIo = new IntersectionObserver((entries) => {{
      for (const en of entries) {{
        if (!en.isIntersecting) continue;
        for (const p of strokeFor.get(en.target) || []) {{
          const len = typeof p.getTotalLength === "function" ? p.getTotalLength() : 0;
          if (len > 0) {{
            p.style.strokeDasharray = String(len);
            p.animate(
              [{{ strokeDashoffset: len }}, {{ strokeDashoffset: 0 }}],
              {{ duration: {_stroke['ms']}, easing: "{_stroke['easing']}", fill: "forwards" }},
            );
          }}
        }}
        strokeIo.unobserve(en.target);
      }}
    }});
    strokeFor.forEach((_list, target) => strokeIo.observe(target));"""
if _state is not None or _stroke is not None:
    _fade_from = _state["from_opacity"] if _state is not None else 0.5
    _fade_to = _state["to_opacity"] if _state is not None else 1
    _fade_ms = _state["ms"] if _state is not None else 800
    _fade_easing = _state["easing"] if _state is not None else "ease"
    _fade_block = ""
    if _state is not None:
        _fade_block = f"""
    const els = Array.from(document.querySelectorAll("[data-scroll-fade]"));
    const io = new IntersectionObserver((entries) => {{
      for (const en of entries) {{
        if (!en.isIntersecting) continue;
        const el = en.target as HTMLElement;
        el.animate(
          [{{ opacity: {json.dumps(_fade_from)} }}, {{ opacity: {json.dumps(_fade_to)} }}],
          {{ duration: {_fade_ms}, easing: "{_fade_easing}", fill: "forwards" }},
        );
        io.unobserve(el);
      }}
    }});
    els.forEach((el) => io.observe(el));"""
    _cleanup = []
    if _state is not None:
        _cleanup.append("io.disconnect();")
    if _stroke is not None:
        _cleanup.append("strokeIo.disconnect();")
    _emit("ScrollStateDriver.tsx", f"""'use client';
import {{ useEffect }} from "react";

/**
 * Deterministically emitted by skills/visual-debug/scripts/emit-scroll-helpers.sh
 * from transition-spec.json — the ref's in-view state animations. The
 * transpiler stamps elements captured at their INACTIVE state
 * (data-scroll-fade: state fade; data-stroke-draw: strokeDashoffset draw-in);
 * this driver animates each to the active state on first viewport entry using
 * the spec's real duration/ease (WAAPI, fill forwards → measurable
 * computed-style delta). Do not hand-edit — re-run the emitter.
 */
export default function ScrollStateDriver() {{
  useEffect(() => {{{_fade_block}{_stroke_block}
    return () => {{ {' '.join(_cleanup)} }};
  }}, []);
  return null;
}}
""")

# ── ScrollScrub.tsx + scrollScrubSites.ts (Fix 101) ──────────────────────────
# plan.scrollScrub carries the ref's CONCRETE scroll-scrub tables (offset window
# + useTransform input/output ranges + bound property) pulled deterministically
# from the bundle by _bundle_extraction.py. This is the scroll-scrubbed
# background scale/zoom (a `scale` band straddling 1.0) and kin. Emit a reusable
# primitive (unconditional useScroll/useTransform/useSpring — render-safe) plus a
# data file of the ref-grounded bands so wiring uses real values, not invented
# ones. Wrap the scrubbed element: <ScrollScrub {...scrollScrubSites[i]}>.
import re as _re

_SCRUB_PROP_KEYS = ("scale", "scaleX", "scaleY", "opacity", "x", "y", "rotate")


def _parse_scrub_range(s):
    """Framer range string -> numeric list. Ternary (cond?[..]:[..]) takes the
    first bracket; returns None if any token isn't a plain number (e.g. a
    media-query var the codegen can't resolve)."""
    if not isinstance(s, str):
        return None
    mm = _re.search(r"\[([^\[\]]*)\]", s)
    if not mm:
        return None
    out = []
    for tok in mm.group(1).split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            return None
    return out if len(out) >= 2 else None


def _scrub_band_plausible(prop, output):
    """Guard against the bundle resolver mis-binding a property: an opacity band
    must stay in [0, 1.5] and a scale band in [0, 8]. Implausible ranges (e.g.
    opacity output [80, 100]) mean the property tag is wrong — drop the band
    rather than emit a broken effect. Positional props (x/y/rotate) are px/deg
    and unconstrained."""
    if prop == "opacity":
        return all(-0.01 <= v <= 1.5 for v in output)
    if prop in ("scale", "scaleX", "scaleY"):
        return all(0 <= v <= 8 for v in output)
    return True


_ss = plan.get("scrollScrub")
if isinstance(_ss, dict) and _ss.get("required"):
    _sites_out = []
    for _site in _ss.get("sites", []) or []:
        if not isinstance(_site, dict):
            continue
        _bands = {}
        _has_scale = False
        for _t in _site.get("transforms", []) or []:
            if not isinstance(_t, dict):
                continue
            _prop = _t.get("property")
            if _prop not in _SCRUB_PROP_KEYS or _prop in _bands:
                continue
            _inp = _parse_scrub_range(_t.get("input"))
            _outp = _parse_scrub_range(_t.get("output"))
            if not _inp or not _outp or len(_inp) != len(_outp):
                continue
            if not _scrub_band_plausible(_prop, _outp):
                continue
            _bands[_prop] = [_inp, _outp]
            if _prop.startswith("scale"):
                _has_scale = True
        if not _bands:
            continue
        _offset = None
        if isinstance(_site.get("offset"), str):
            try:
                _parsed = json.loads(_site["offset"])
                if isinstance(_parsed, list) and len(_parsed) == 2:
                    _offset = _parsed
            except (json.JSONDecodeError, ValueError):
                _offset = None
        _entry = dict(_bands)
        if _offset is not None:
            _entry["offset"] = _offset
        # scale-zoom backgrounds in these bundles smooth the scale with a spring
        # (the extractor resolved scale through a useSpring hop); mirror that.
        if _has_scale:
            _entry["spring"] = True
        _sites_out.append(_entry)

    if _sites_out:
        _emit("scrollScrubSites.ts", (
            "/**\n"
            " * Deterministically emitted by skills/visual-debug/scripts/emit-scroll-helpers.sh\n"
            " * from generation-plan.json -> scrollScrub: the ref's REAL scroll-scrub\n"
            " * bands (offset window + useTransform input/output) extracted from the\n"
            " * bundle, NOT invented. Each entry is spreadable into <ScrollScrub>.\n"
            " * Wrap the scrubbed element (e.g. a background that scales on scroll):\n"
            " *   <ScrollScrub {...scrollScrubSites[0]}>...</ScrollScrub>\n"
            " * Do not hand-edit — re-run the emitter to refresh.\n"
            " */\n"
            "export type ScrubBand = [number[], number[]];\n"
            "export interface ScrubSite {\n"
            "  offset?: [string, string];\n"
            "  scale?: ScrubBand;\n"
            "  scaleX?: ScrubBand;\n"
            "  scaleY?: ScrubBand;\n"
            "  opacity?: ScrubBand;\n"
            "  x?: ScrubBand;\n"
            "  y?: ScrubBand;\n"
            "  rotate?: ScrubBand;\n"
            "  spring?: boolean;\n"
            "}\n\n"
            "export const scrollScrubSites: ScrubSite[] = "
            + json.dumps(_sites_out, ensure_ascii=False)
            + ";\n"
        ))
        _emit("ScrollScrub.tsx", '''"use client";

import { useRef } from "react";
import type { ReactNode } from "react";
import { motion, useScroll, useTransform, useSpring } from "framer-motion";
import type { ScrubBand } from "./scrollScrubSites";

/**
 * Deterministically emitted by skills/visual-debug/scripts/emit-scroll-helpers.sh
 * from generation-plan.json -> scrollScrub. Maps the wrapped element's own scroll
 * progress (useScroll target+offset, tracks Lenis) onto motion properties via
 * useTransform with the ref's REAL input/output bands. `spring` wraps the output
 * in useSpring (matches sites whose scale was smoothed by a spring — the
 * scroll-scrubbed background zoom). Hooks are called unconditionally (render-safe);
 * only provided bands are attached to style. Do not hand-edit — re-run the emitter.
 */
interface ScrollScrubProps {
  children: ReactNode;
  className?: string;
  offset?: [string, string];
  scale?: ScrubBand;
  scaleX?: ScrubBand;
  scaleY?: ScrubBand;
  opacity?: ScrubBand;
  x?: ScrubBand;
  y?: ScrubBand;
  rotate?: ScrubBand;
  spring?: boolean;
}

export default function ScrollScrub({
  children,
  className,
  offset = ["start end", "end start"],
  scale,
  scaleX,
  scaleY,
  opacity,
  x,
  y,
  rotate,
  spring = false,
}: ScrollScrubProps) {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset });

  const id = (b: ScrubBand | undefined, d: number) =>
    b ? b : ([[0, 1], [d, d]] as ScrubBand);
  const mvScale = useTransform(scrollYProgress, ...id(scale, 1));
  const mvScaleX = useTransform(scrollYProgress, ...id(scaleX, 1));
  const mvScaleY = useTransform(scrollYProgress, ...id(scaleY, 1));
  const mvOpacity = useTransform(scrollYProgress, ...id(opacity, 1));
  const mvX = useTransform(scrollYProgress, ...id(x, 0));
  const mvY = useTransform(scrollYProgress, ...id(y, 0));
  const mvRotate = useTransform(scrollYProgress, ...id(rotate, 0));

  const spScale = useSpring(mvScale, { stiffness: 120, damping: 30 });
  const spScaleX = useSpring(mvScaleX, { stiffness: 120, damping: 30 });
  const spScaleY = useSpring(mvScaleY, { stiffness: 120, damping: 30 });

  const style: Record<string, unknown> = {};
  if (scale) style.scale = spring ? spScale : mvScale;
  if (scaleX) style.scaleX = spring ? spScaleX : mvScaleX;
  if (scaleY) style.scaleY = spring ? spScaleY : mvScaleY;
  if (opacity) style.opacity = mvOpacity;
  if (x) style.x = mvX;
  if (y) style.y = mvY;
  if (rotate) style.rotate = mvRotate;

  return (
    <motion.div ref={ref} className={className} data-scroll-scrub="1" style={style}>
      {children}
    </motion.div>
  );
}
''')


# ── ScrollWordHighlight.tsx (Fix 103) ────────────────────────────────────────
# When generation-plan.signatureEffects declares a per-word scroll highlight (the
# ref toggles words/lines between a highlighted and a dimmed colour as scroll
# progress advances — detected deterministically by _signature_effects.py), emit
# a reusable primitive. It splits text into words, maps the wrapped element's
# scrollYProgress onto an active word count, and colours words up to that index.
# The agent supplies the real highlight/dim colours or the preserved CSS-module
# class names; the progress→index mapping is even-distribution (visually close to
# the ref's responsive thresholds). Wrap the target text:
#   <ScrollWordHighlight text="..." highlightColor="#111" dimColor="#bbb" />
_effects = plan.get("signatureEffects")
_has_per_word = isinstance(_effects, list) and any(
    isinstance(e, dict) and (
        "per-word" in str(e.get("effectType") or "").lower()
        or (isinstance(e.get("animation"), dict) and e["animation"].get("perWord"))
    )
    for e in _effects
)
if _has_per_word:
    _emit("ScrollWordHighlight.tsx", '''"use client";

import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { useScroll, useMotionValueEvent } from "framer-motion";

/**
 * Deterministically emitted by skills/visual-debug/scripts/emit-scroll-helpers.sh
 * from generation-plan.signatureEffects (per-word-scroll-highlight). As the
 * wrapped element scrolls, words are highlighted up to an active index derived
 * from scrollYProgress — the ref's scroll-progress per-word colour change. Pass
 * the real colours or the preserved CSS-module class names. `children` (a plain
 * string) is preferred; `text` is a fallback. Do not hand-edit — re-run the emitter.
 */
interface ScrollWordHighlightProps {
  children?: ReactNode;
  text?: string;
  className?: string;
  highlightClassName?: string;
  dimClassName?: string;
  highlightColor?: string;
  dimColor?: string;
  offset?: [string, string];
}

export default function ScrollWordHighlight({
  children,
  text,
  className,
  highlightClassName,
  dimClassName,
  highlightColor = "inherit",
  dimColor = "rgba(0,0,0,0.25)",
  offset = ["start end", "end start"],
}: ScrollWordHighlightProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const source = typeof children === "string" ? children : text || "";
  const words = source.split(" ");
  const { scrollYProgress } = useScroll({ target: ref, offset });
  const [active, setActive] = useState(0);
  useMotionValueEvent(scrollYProgress, "change", (p) => {
    setActive(Math.round(p * words.length));
  });
  return (
    <span ref={ref} className={className} data-scroll-word-highlight="1">
      {words.map((w, i) => {
        const on = i < active;
        const cls = on ? highlightClassName : dimClassName;
        const style = cls
          ? undefined
          : { color: on ? highlightColor : dimColor, transition: "color 0.2s ease" };
        return (
          <span key={i} className={cls} style={style}>
            {w}
            {i < words.length - 1 ? " " : ""}
          </span>
        );
      })}
    </span>
  );
}
''')


if emitted:
    print(f"✓ emit-scroll-helpers: wrote {', '.join(emitted)} → {lib}")
else:
    print("▸ emit-scroll-helpers: no scroll helpers required — nothing emitted")
PY
