from __future__ import annotations

import json
import math
import re as _re
import sys
from pathlib import Path
from typing import Any, cast

plan_path = Path(sys.argv[1])
impl_dir = Path(sys.argv[2])

if not plan_path.exists():
    # A ref can carry a transition-spec and no generation-plan. The scaffold
    # still mounts drivers it derives from the spec, so skipping outright left
    # those mounts with no file behind them — a tree that cannot build. Proceed
    # with an empty plan so the emitted-ground-truth branches below can still
    # write what the scaffold already committed to mounting.
    plan: Any = {}
else:
    try:
        plan = json.loads(plan_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        # An unreadable or malformed plan is a real fault, not an absent one.
        print(f"ERROR: cannot read {plan_path}: {e}", file=sys.stderr)
        sys.exit(2)

if not isinstance(plan, dict):
    print("▸ emit-scroll-helpers: malformed plan — nothing emitted")
    sys.exit(0)

lib = impl_dir / "src" / "lib"
emitted = []


def _is_number(value: object) -> bool:
    """Runtime-safe numeric check for Python 3.9 through current releases."""
    return isinstance(value, float) or (
        isinstance(value, int) and not isinstance(value, bool)
    )


def _emit(filename: str, body: str) -> None:
    lib.mkdir(parents=True, exist_ok=True)
    (lib / filename).write_text(body, encoding="utf-8")
    emitted.append(filename)


def _scaffold_references(name: str) -> bool:
    """Does the ALREADY-EMITTED tree reference this helper?

    Mount implies emit. The scaffold decides some drivers from transition-spec
    while this emitter decides them from generation-plan, and when those two
    signals disagree the scaffold emits `<ScrollReveal>` / `<ScrollStateDriver>`
    with no file behind it — a tree that cannot build, which nothing noticed.
    This emitter runs AFTER the scaffold, so instead of mirroring the other
    predicate (which has to be re-synced forever, and silently reopens on the
    next driver) it asks the emitted output directly. A referenced helper is
    needed by definition: the scaffold already committed to mounting it.
    """
    src = impl_dir / "src"
    if not src.is_dir():
        return False
    needle_tag = f"<{name}"
    needle_import = f"/{name}"
    for path in src.rglob("*"):
        if path.suffix not in (".tsx", ".ts", ".jsx", ".js") or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if needle_tag in text or needle_import in text:
            return True
    return False


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
    config = (
        cast(dict[str, Any], ss.get("config"))
        if isinstance(ss.get("config"), dict)
        else {}
    )
    opt_lines = []
    for k in _NUMERIC_BOOL_KEYS:
        if k not in config:
            continue
        v = config[k]
        if isinstance(v, bool) or _is_number(v):
            opt_lines.append(f"      {k}: {json.dumps(v)},")
    options_block = "\n".join(opt_lines)
    lenis_ctor = f"new Lenis({{\n{options_block}\n    }})" if options_block else "new Lenis()"
    _emit("SmoothScroll.tsx", f""""use client";
import {{ useEffect }} from "react";
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
    // Publish the real instance so the transition-fires scrub re-probe can drive
    // the engine's virtual scroll (window.__lenis.scrollTo) and tell a DEAD scrub
    // from a genuinely unmeasurable one. window.lenis is often a {{version}} decoy,
    // so the re-probe reads window.__lenis specifically — expose that handle.
    (window as any).__lenis = lenis;
    const notifyScroll = () => window.dispatchEvent(new Event("ui-clone-scroll"));
    lenis.on("scroll", notifyScroll);
    // gen-H2 — honor the capture flag: section_capture forces native scrollTo to
    // crop exact ref frames, but a running Lenis raf loop reverts that forced
    // scroll (actualY -> 0, wrong-frame crops on Lenis sites). While capturing
    // (or under prefers-reduced-motion) stop the engine and let native scroll
    // stand; the __lenis handle stays exposed for the scrub re-probe.
    const capturing = (window as any).__UI_CLONE_CAPTURE__ === true ||
      (typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    let raf = 0;
    if (capturing) {{
      lenis.stop();
    }} else {{
      const loop = (time: number) => {{
        lenis.raf(time);
        raf = requestAnimationFrame(loop);
      }};
      raf = requestAnimationFrame(loop);
    }}
    return () => {{
      cancelAnimationFrame(raf);
      lenis.off("scroll", notifyScroll);
      lenis.destroy();
      (window as any).__lenis = null;
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


def _spec_reveal_entry() -> dict[str, Any] | None:
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
        def _num(v: Any, default: float) -> float:
            return v if _is_number(v) else default
        ease = None
        try:
            parsed = json.loads(str(anim.get("ease") or ""))
            if isinstance(parsed, list) and len(parsed) == 4 and all(
                _is_number(x) for x in parsed
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
elif (isinstance(sd, dict) and sd.get("required")) or _scaffold_references("ScrollReveal"):
    _emit("ScrollReveal.tsx", """'use client';

import { useEffect, useRef, useState } from "react";
import { motion, useScroll, useTransform } from "framer-motion";

/**
 * Deterministically emitted by skills/visual-debug/scripts/emit-scroll-helpers.sh
 * from generation-plan.json -> scrollDriven (Framer useScroll/useTransform).
 * Render-verified pattern: maps the element's own scroll progress onto opacity
 * + translateY. Wrap reveal sections in <ScrollReveal>. Tracks Lenis-driven
 * scroll automatically (Lenis updates document scrollTop, which useScroll
 * observes). Do not hand-edit — re-run the emitter to refresh.
 *
 * Above-the-fold latch: a section already inside the first viewport at load
 * never receives the scroll delta that would settle it, so the scrub pins it at
 * the from-state (opacity 0, y 60) forever — every capture of that section then
 * renders blank and section-compare reports a catastrophic AE that has nothing
 * to do with the section's markup. The reference settles those sections at
 * load, so latch them to the to-state once they have been inside the viewport.
 * The latch is one-way (a reveal never un-reveals) and is driven by the same
 * scroll listener as the scrub, NOT by a per-element IntersectionObserver.
 */
export default function ScrollReveal({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [settled, setSettled] = useState(false);
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ["start end", "start center"],
  });
  const opacity = useTransform(scrollYProgress, [0, 1], [0, 1]);
  const y = useTransform(scrollYProgress, [0, 1], [60, 0]);
  useEffect(() => {
    if (settled) return;
    const el = ref.current;
    if (!el) return;
    const capture = Boolean(
      (window as unknown as { __UI_CLONE_CAPTURE__?: boolean }).__UI_CLONE_CAPTURE__,
    );
    if (capture) {
      setSettled(true);
      return;
    }
    const check = () => {
      const r = el.getBoundingClientRect();
      if (r.top < window.innerHeight * 0.5) setSettled(true);
    };
    check();
    window.addEventListener("scroll", check, { passive: true });
    window.addEventListener("resize", check, { passive: true });
    return () => {
      window.removeEventListener("scroll", check);
      window.removeEventListener("resize", check);
    };
  }, [settled]);
  return (
    <motion.div
      ref={ref}
      className={className}
      data-scroll-reveal="1"
      data-reveal-settled={settled ? "1" : undefined}
      style={settled ? { opacity: 1, y: 0 } : { opacity, y }}
    >
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


def _spec_state_fade_entry() -> dict[str, Any] | None:
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
        if not (_is_number(fo) and _is_number(to_o)):
            continue
        from_opacity = float(cast(float, fo))
        to_opacity = float(cast(float, to_o))
        if not 0 < from_opacity < 1:
            continue
        dur = anim.get("duration")
        duration = float(cast(float, dur)) if _is_number(dur) else 0.8
        easing = "ease"
        try:
            parsed = json.loads(str(anim.get("ease") or ""))
            if isinstance(parsed, list) and len(parsed) == 4 and all(
                _is_number(x) for x in parsed
            ):
                easing = "cubic-bezier(" + ", ".join(str(x) for x in parsed) + ")"
        except (json.JSONDecodeError, ValueError):
            pass
        return {
            "from_opacity": from_opacity,
            "to_opacity": to_opacity,
            "ms": int(round(duration * 1000)),
            "easing": easing,
        }
    return None


# ── stroke-draw entry (Fix 76) — strokeDashoffset draw-in for SVG paths ─────


def _spec_stroke_draw_entry() -> dict[str, Any] | None:
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
        duration = float(cast(float, dur)) if _is_number(dur) else 1.0
        easing = "ease"
        try:
            parsed = json.loads(str(anim.get("ease") or "")) if isinstance(anim, dict) else None
            if isinstance(parsed, list) and len(parsed) == 4 and all(
                _is_number(x) for x in parsed
            ):
                easing = "cubic-bezier(" + ", ".join(str(x) for x in parsed) + ")"
        except (json.JSONDecodeError, ValueError):
            pass
        return {"ms": int(round(duration * 1000)), "easing": easing}
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
if _state is not None or _stroke is not None or _scaffold_references("ScrollStateDriver"):
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


# ── Scroll/Hover class-toggle helpers ──────────────────────────────────────
# Preserve discrete class-name state changes from transition-spec entries.
# Scroll transitions map to scroll threshold checks; hover transitions map to
# hover enter/leave listeners.
def _spec_class_toggles() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    spec_path = plan_path.parent / "transition-spec.json"
    try:
        spec = json.loads(spec_path.read_text())
    except (OSError, json.JSONDecodeError):
        return [], []
    transitions = spec.get("transitions") if isinstance(spec, dict) else None
    if not isinstance(transitions, list):
        return [], []
    scroll_toggles: list[dict[str, Any]] = []
    hover_toggles: list[dict[str, Any]] = []
    threshold_re = _re.compile(
        r"^\s*window\.scrollY\s*(>=|<=|>|<)\s*(-?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
    )
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        animation = transition.get("animation")
        if not isinstance(animation, dict):
            continue
        trigger = str(transition.get("trigger") or "").lower()
        kind = str(animation.get("type") or transition.get("type") or "").lower()
        prop = str(animation.get("property") or "").lower()
        if not (
            "scroll" in trigger or "hover" in trigger
        ) or not (
            "class-toggle" in kind or "classname" in prop
        ):
            continue
        target = transition.get("target") or transition.get("selector")
        before = animation.get("from")
        after = animation.get("to")
        before_class = before.get("className") if isinstance(before, dict) else None
        after_class = after.get("className") if isinstance(after, dict) else None
        if not (
            isinstance(target, str)
            and target.strip()
            and isinstance(before_class, str)
            and isinstance(after_class, str)
        ):
            continue
        before_tokens = before_class.split()
        after_tokens = after_class.split()
        add = [token for token in after_tokens if token not in before_tokens]
        remove = [token for token in before_tokens if token not in after_tokens]
        if not add and not remove:
            continue
        entry: dict[str, Any] = {"target": target.strip(), "add": add, "remove": remove}
        if "scroll" in trigger:
            match = threshold_re.match(str(animation.get("threshold") or ""))
            if match is None:
                continue
            entry["operator"] = match.group(1)
            entry["threshold"] = float(match.group(2))
            scroll_toggles.append(entry)
            continue
        if "hover" in trigger:
            hover_toggles.append(entry)
    return scroll_toggles, hover_toggles


_scroll_class_toggles, _hover_class_toggles = _spec_class_toggles()
if _scroll_class_toggles:
    _toggle_blocks = []
    for _index, _toggle in enumerate(_scroll_class_toggles):
        _threshold = _toggle["threshold"]
        _threshold_js = (
            str(int(_threshold)) if float(_threshold).is_integer() else str(_threshold)
        )
        _toggle_blocks.append(f"""
      // transition-spec scroll class toggle #{_index}
      try {{
        const active = window.scrollY {_toggle['operator']} {_threshold_js};
        document.querySelectorAll({json.dumps(_toggle['target'])}).forEach((node) => {{
          const element = node as HTMLElement;
          for (const className of {json.dumps(_toggle['add'])}) {{
            element.classList.toggle(className, active);
          }}
          for (const className of {json.dumps(_toggle['remove'])}) {{
            element.classList.toggle(className, !active);
          }}
        }});
      }} catch {{
        // Selector syntax is validated upstream; keep one bad entry isolated.
      }}""")
    _emit("ScrollClassToggleDriver.tsx", f'''"use client";

import {{ useEffect }} from "react";

/** Replays discrete scroll/class state declared in transition-spec.json. */
export default function ScrollClassToggleDriver() {{
  useEffect(() => {{
    let raf = 0;
    const apply = () => {{{''.join(_toggle_blocks)}
    }};
    const schedule = () => {{
      if (raf) return;
      raf = requestAnimationFrame(() => {{
        raf = 0;
        apply();
      }});
    }};
    apply();
    window.addEventListener("scroll", schedule, {{ passive: true }});
    return () => {{
      window.removeEventListener("scroll", schedule);
      cancelAnimationFrame(raf);
    }};
  }}, []);
  return null;
}}
''')

if _hover_class_toggles:
    _toggle_blocks = []
    for _index, _toggle in enumerate(_hover_class_toggles):
        _toggle_blocks.append(f"""
    // transition-spec hover class toggle #{_index}
    const _selectors = {json.dumps(_toggle['target'])};
    document.querySelectorAll(_selectors).forEach((node) => {{
      const element = node as HTMLElement;
      if (!element || !(element instanceof Element)) {{
        return;
      }}
      const onEnter = () => {{
        {''.join(f'element.classList.add({json.dumps(class_name)});' for class_name in _toggle['add'])}
        {''.join(f'element.classList.remove({json.dumps(class_name)});' for class_name in _toggle['remove'])}
      }};
      const onLeave = () => {{
        {''.join(f'element.classList.remove({json.dumps(class_name)});' for class_name in _toggle['add'])}
        {''.join(f'element.classList.add({json.dumps(class_name)});' for class_name in _toggle['remove'])}
      }};
      element.addEventListener("mouseenter", onEnter);
      element.addEventListener("mouseleave", onLeave);
      element.addEventListener("focusin", onEnter);
      element.addEventListener("focusout", onLeave);
      hoverHandlers.push({{
        node: element,
        onEnter,
        onLeave,
      }});
    }});""")
    _emit("HoverClassToggleDriver.tsx", f'''"use client";

import {{ useEffect }} from "react";

interface HoverClassToggle {{
  node: HTMLElement;
  onEnter: () => void;
  onLeave: () => void;
}}

/** Replays discrete hover/class state declared in transition-spec.json. */
export default function HoverClassToggleDriver() {{
  useEffect(() => {{
    const hoverHandlers: HoverClassToggle[] = [];
    const teardown = () => {{
      for (const item of hoverHandlers) {{
        const {{ node, onEnter, onLeave }} = item;
        node.removeEventListener("mouseenter", onEnter);
        node.removeEventListener("mouseleave", onLeave);
        node.removeEventListener("focusin", onEnter);
        node.removeEventListener("focusout", onLeave);
      }}
    }};
    {{{''.join(_toggle_blocks)}
    }}
    return () => {{
      teardown();
    }};
  }}, []);
  return null;
}}
''')

# ── ScrollScrub.tsx + scrollScrubSites.ts (Fix 101) ──────────────────────────
# plan.scrollScrub carries the ref's CONCRETE scroll-scrub tables (offset window
# + useTransform input/output ranges + bound property) pulled deterministically
# from the bundle by _bundle_extraction.py. This is the scroll-scrubbed
# background scale/zoom (a `scale` band straddling 1.0) and kin. Emit a reusable
# primitive (unconditional useScroll/useTransform/useSpring — render-safe) plus a
# data file of the ref-grounded bands so wiring uses real values, not invented
# ones. Wrap the scrubbed element: <ScrollScrub {...scrollScrubSites[i]}>.
_SCRUB_PROP_KEYS = (
    "scale", "scaleX", "scaleY", "opacity", "x", "y", "rotate",
    "width", "height", "borderRadius",
)
_LINKED_SCRUB_PROP_KEYS = (*_SCRUB_PROP_KEYS, "blur", "brightness")
_STATE_MACHINE_PIXEL_PROP_UNITS = {"top": "px"}


def _parse_scrub_range(s: Any) -> list[float] | None:
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


def _normalized_scrub_input(values: list[float]) -> bool:
    """Scrub emitters consume scroll progress, so input domains must be [0, 1]."""
    return (
        all(0 <= value <= 1 for value in values)
        and all(left <= right for left, right in zip(values, values[1:]))
    )


def _finite_ascending_input(values: list[float]) -> bool:
    return all(math.isfinite(value) for value in values) and all(
        left <= right for left, right in zip(values, values[1:])
    )


def _finite_values(values: list[float]) -> bool:
    return all(math.isfinite(value) for value in values)


def _scrub_band_plausible(prop: str, output: list[float]) -> bool:
    """Guard against the bundle resolver mis-binding a property: an opacity band
    must stay in [0, 1.5] and a scale band in [0, 8]. Implausible ranges (e.g.
    opacity output [80, 100]) mean the property tag is wrong — drop the band
    rather than emit a broken effect. Blur is replayed only as plain CSS
    blur(px), capped at 200px to avoid fabricating pathological filter states.
    Positional props (x/y/rotate) are px/deg and unconstrained."""
    if not _finite_values(output):
        return False
    if prop == "opacity":
        return all(-0.01 <= v <= 1.5 for v in output)
    if prop in ("scale", "scaleX", "scaleY"):
        return all(0 <= v <= 8 for v in output)
    if prop == "blur":
        return all(0 <= v <= 200 for v in output)
    if prop == "brightness":
        return all(0 <= v for v in output)
    if prop in ("width", "height", "borderRadius"):
        return True
    return True


_ss = plan.get("scrollScrub")
_linked_sites: list[dict[str, Any]] = []
if isinstance(_ss, dict) and _ss.get("required"):
    # Runtime sampling is keyed to document scroll progress and can contain
    # several descendant selectors (including duplicate selectors with distinct
    # curves). A section-level <ScrollScrub> wrapper targets the wrong element;
    # emit a selector-scoped singleton for these sites instead.
    for _site in _ss.get("sites", []) or []:
        if not isinstance(_site, dict):
            continue
        _progress_source = str(_site.get("progressSource") or "")
        if _progress_source not in {
            "document", "document-progress", "target-offset"
        }:
            continue
        _selector = _site.get("selector")
        if not isinstance(_selector, str) or not _selector.strip():
            continue
        _bands: dict[str, Any] = {}
        _units: dict[str, str] = {}
        for _t in _site.get("transforms", []) or []:
            if not isinstance(_t, dict):
                continue
            _prop = _t.get("property")
            if _prop not in _LINKED_SCRUB_PROP_KEYS or _prop in _bands:
                continue
            _inp = _parse_scrub_range(_t.get("input"))
            _outp = _parse_scrub_range(_t.get("output"))
            if not _inp or not _outp or len(_inp) != len(_outp):
                continue
            if not _normalized_scrub_input(_inp):
                continue
            if not _scrub_band_plausible(_prop, _outp):
                continue
            _bands[_prop] = [_inp, _outp]
            _unit = _t.get("unit")
            if (
                _prop in {"width", "height", "borderRadius"}
                and _unit in {"", "px", "%", "vw"}
            ):
                _units[_prop] = _unit
        if not _bands:
            continue
        _selector_index = _site.get("selectorIndex")
        if not isinstance(_selector_index, int) or _selector_index < 0:
            _selector_index = 0
        _linked = {
            "selector": _selector.strip(),
            "selectorIndex": _selector_index,
            "inputDomain": "progress",
            "progressSource": (
                "target-offset"
                if _progress_source == "target-offset"
                else "document-progress"
            ),
            "bands": _bands,
        }
        if _site.get("replay") == "all-matches":
            _linked["replay"] = "all-matches"
            _source_ids = [
                _source_id
                for _source_id in (_site.get("sourceIds") or [])
                if isinstance(_source_id, str) and _source_id.strip()
            ]
            if _source_ids:
                _linked["sourceIds"] = _source_ids
        if _units:
            _linked["units"] = _units
        _scope = _site.get("scope")
        if isinstance(_scope, str) and _scope.strip():
            _linked["scope"] = _scope.strip()
        _media = _site.get("media")
        if isinstance(_media, str) and _media.strip():
            _linked["media"] = _media.strip()
        _offset = _site.get("offset")
        if isinstance(_offset, str):
            try:
                _offset = json.loads(_offset)
            except (json.JSONDecodeError, ValueError):
                _offset = None
        if (
            isinstance(_offset, list)
            and len(_offset) == 2
            and all(isinstance(_value, str) for _value in _offset)
        ):
            _linked["offset"] = _offset
        _linked_sites.append(_linked)

_sm = plan.get("scrollStateMachine")
if isinstance(_sm, dict) and _sm.get("required"):
    for _site in _sm.get("sites", []) or []:
        if not isinstance(_site, dict):
            continue
        if _site.get("inputDomain") != "scroll-y-px":
            continue
        _selector = _site.get("selector")
        if not isinstance(_selector, str) or not _selector.strip():
            continue
        _state_bands: dict[str, Any] = {}
        _state_units: dict[str, str] = {}
        for _t in _site.get("transforms", []) or []:
            if not isinstance(_t, dict):
                continue
            _prop = _t.get("property")
            if _prop not in _STATE_MACHINE_PIXEL_PROP_UNITS or _prop in _state_bands:
                continue
            _unit = _t.get("unit")
            if _unit != _STATE_MACHINE_PIXEL_PROP_UNITS[_prop]:
                continue
            _inp = _parse_scrub_range(_t.get("input"))
            _outp = _parse_scrub_range(_t.get("output"))
            if not _inp or not _outp or len(_inp) != len(_outp):
                continue
            if not _finite_ascending_input(_inp) or not _finite_values(_outp):
                continue
            _state_bands[_prop] = [_inp, _outp]
            _state_units[_prop] = _unit
        if not _state_bands:
            continue
        _selector_index = _site.get("selectorIndex")
        if not isinstance(_selector_index, int) or _selector_index < 0:
            _selector_index = 0
        _linked_sites.append({
            "selector": _selector.strip(),
            "selectorIndex": _selector_index,
            "inputDomain": "scroll-y-px",
            "bands": _state_bands,
            "units": _state_units,
        })

if _linked_sites:
    _emit("scrollLinkedStyleSites.ts", (
        "/** Runtime-measured scroll-linked style curves. */\n"
        'import type { UseScrollOptions } from "framer-motion";\n'
        "export type LinkedBand = [number[], number[]];\n"
        "export interface ScrollLinkedStyleSite {\n"
        "  selector: string;\n"
        "  selectorIndex: number;\n"
        '  replay?: "all-matches";\n'
        "  sourceIds?: string[];\n"
        "  scope?: string;\n"
        "  media?: string;\n"
        '  inputDomain: "progress" | "scroll-y-px";\n'
        '  progressSource?: "document-progress" | "target-offset";\n'
        "  offset?: UseScrollOptions[\"offset\"];\n"
        "  bands: Record<string, LinkedBand>;\n"
        "  units?: Record<string, string>;\n"
        "}\n\n"
        "export const scrollLinkedStyleSites: ScrollLinkedStyleSite[] = "
        + json.dumps(_linked_sites, ensure_ascii=False)
        + ";\n"
    ))
    _emit("ScrollLinkedStyleDriver.tsx", '''"use client";

import { useEffect } from "react";
import { scrollLinkedStyleSites } from "./scrollLinkedStyleSites";
import type { LinkedBand, ScrollLinkedStyleSite } from "./scrollLinkedStyleSites";

function interpolate(progress: number, band: LinkedBand): number {
  const [input, output] = band;
  if (progress <= input[0]) return output[0];
  const last = input.length - 1;
  if (progress >= input[last]) return output[last];
  for (let index = 1; index < input.length; index += 1) {
    if (progress > input[index]) continue;
    const span = input[index] - input[index - 1];
    const ratio = span === 0 ? 1 : (progress - input[index - 1]) / span;
    return output[index - 1] + (output[index] - output[index - 1]) * ratio;
  }
  return output[last];
}

function applyBandStyles(
  style: CSSStyleDeclaration,
  progress: number,
  bands: Record<string, LinkedBand>,
  units: Record<string, string> = {},
) {
  const value = (name: string) => bands[name] && interpolate(progress, bands[name]);
  const length = (name: string) => `${value(name)}${units[name] ?? "px"}`;
  // A stylesheet !important rule outranks a plain inline write, so a single
  // authored pin silently defeats the whole band: the driver keeps writing and
  // nothing moves. These values are the ref's own measured motion for the
  // property, so they are written at a priority a pin cannot outrank.
  const set = (property: string, next: string) =>
    style.setProperty(property, next, "important");
  if (bands.opacity) set("opacity", String(value("opacity")));
  if (bands.width) set("width", length("width"));
  if (bands.height) set("height", length("height"));
  if (bands.borderRadius) set("border-radius", length("borderRadius"));
  if (bands.top) set("top", length("top"));
  if (bands.blur && bands.brightness) {
    set("filter", `blur(${value("blur")}px) brightness(${value("brightness")})`);
  } else if (bands.blur) set("filter", `blur(${value("blur")}px)`);

  const transforms: string[] = [];
  if (bands.x) transforms.push(`translateX(${value("x")}px)`);
  if (bands.y) transforms.push(`translateY(${value("y")}px)`);
  if (bands.rotate) transforms.push(`rotate(${value("rotate")}deg)`);
  if (bands.scale) transforms.push(`scale(${value("scale")})`);
  if (bands.scaleX) transforms.push(`scaleX(${value("scaleX")})`);
  if (bands.scaleY) transforms.push(`scaleY(${value("scaleY")})`);
  if (transforms.length) set("transform", transforms.join(" "));
}

type InlineStyleSnapshot = { value: string; priority: string };
type StyledTarget = HTMLElement | SVGElement;
type OriginalStyles = Map<
  StyledTarget,
  Map<string, InlineStyleSnapshot>
>;
type ActiveProperties = Map<StyledTarget, Set<string>>;
type StyleApplication = {
  target: StyledTarget;
  progress: number;
  bands: Record<string, LinkedBand>;
  units?: Record<string, string>;
};

function bandStyleProperties(bands: Record<string, LinkedBand>): string[] {
  const properties = new Set<string>();
  if (bands.opacity) properties.add("opacity");
  if (bands.width) properties.add("width");
  if (bands.height) properties.add("height");
  if (bands.borderRadius) properties.add("border-radius");
  if (bands.top) properties.add("top");
  if (bands.blur || bands.brightness) properties.add("filter");
  if (
    bands.x ||
    bands.y ||
    bands.rotate ||
    bands.scale ||
    bands.scaleX ||
    bands.scaleY
  ) {
    properties.add("transform");
  }
  return Array.from(properties);
}

function rememberBandStyles(
  originalStyles: OriginalStyles,
  target: StyledTarget,
  bands: Record<string, LinkedBand>,
) {
  let saved = originalStyles.get(target);
  if (!saved) {
    saved = new Map();
    originalStyles.set(target, saved);
  }
  const { style } = target;
  for (const property of bandStyleProperties(bands)) {
    if (saved.has(property)) continue;
    saved.set(property, {
      value: style.getPropertyValue(property),
      priority: style.getPropertyPriority(property),
    });
  }
}

function noteActiveBandProperties(
  activeProperties: ActiveProperties,
  target: StyledTarget,
  bands: Record<string, LinkedBand>,
) {
  let properties = activeProperties.get(target);
  if (!properties) {
    properties = new Set();
    activeProperties.set(target, properties);
  }
  for (const property of bandStyleProperties(bands)) {
    properties.add(property);
  }
}

function restoreInactiveBandStyles(
  originalStyles: OriginalStyles,
  activeProperties: ActiveProperties,
) {
  for (const [target, saved] of originalStyles) {
    const active = activeProperties.get(target);
    const { style } = target;
    for (const [property, snapshot] of saved) {
      if (active?.has(property)) continue;
      if (snapshot.value) {
        style.setProperty(property, snapshot.value, snapshot.priority);
      } else {
        style.removeProperty(property);
      }
      saved.delete(property);
    }
    if (!saved.size) originalStyles.delete(target);
  }
}

function restoreAllBandStyles(originalStyles: OriginalStyles) {
  for (const [target, saved] of originalStyles) {
    const { style } = target;
    for (const [property, snapshot] of saved) {
      if (snapshot.value) {
        style.setProperty(property, snapshot.value, snapshot.priority);
      } else {
        style.removeProperty(property);
      }
    }
  }
  originalStyles.clear();
}

function mediaMatches(media?: string): boolean {
  if (!media) return true;
  try {
    return window.matchMedia(media).matches;
  } catch {
    return false;
  }
}

function selectScopedCandidates(
  root: ParentNode,
  selector: string,
  selectorIndex: number,
  replay?: "all-matches",
): Element[] {
  const descendants = Array.from(root.querySelectorAll(selector));
  const candidates =
    root instanceof Element && root.matches(selector)
      ? [root, ...descendants]
      : descendants;
  if (replay === "all-matches") return candidates;
  if (root instanceof Element && root.matches(selector)) {
    const selected = [root, ...descendants][selectorIndex];
    return selected ? [selected] : [];
  }
  const selected = descendants[selectorIndex];
  return selected ? [selected] : [];
}

const anchorFractions: Record<string, number> = {
  start: 0,
  center: 0.5,
  end: 1,
};

function alignmentScrollY(target: Element, offset: string): number | undefined {
  const [targetAnchor, viewportAnchor, ...extra] = offset.trim().split(" ").filter(Boolean);
  if (extra.length) return undefined;
  const targetFraction = anchorFractions[targetAnchor];
  const viewportFraction = anchorFractions[viewportAnchor];
  if (targetFraction === undefined || viewportFraction === undefined) {
    return undefined;
  }
  const rect = target.getBoundingClientRect();
  const documentTop = rect.top + window.scrollY;
  return (
    documentTop + rect.height * targetFraction - window.innerHeight * viewportFraction
  );
}

function documentProgress(): number {
  const scrollRange = Math.max(
    1,
    document.documentElement.scrollHeight - window.innerHeight,
  );
  return Math.min(1, Math.max(0, window.scrollY / scrollRange));
}

function targetOffsetProgress(
  target: Element,
  offset: [string, string],
): number | undefined {
  const start = alignmentScrollY(target, offset[0]);
  const end = alignmentScrollY(target, offset[1]);
  if (start === undefined || end === undefined || Math.abs(end - start) < 1) {
    return undefined;
  }
  return Math.min(1, Math.max(0, (window.scrollY - start) / (end - start)));
}

function siteProgress(
  inputDomain: "progress" | "scroll-y-px",
  progressSource: "document-progress" | "target-offset",
  offset: ScrollLinkedStyleSite["offset"],
  root: ParentNode,
): number {
  switch (inputDomain) {
    case "scroll-y-px":
      return Number.isFinite(window.scrollY) ? Math.max(0, window.scrollY) : 0;
    default: {
      const hasOffsetPair = Array.isArray(offset) && offset.length >= 2;
      if (
        progressSource !== "target-offset" ||
        !hasOffsetPair ||
        !(root instanceof Element)
      ) {
        return documentProgress();
      }
      const startOffset = offset[0];
      const endOffset = offset[1];
      if (
        typeof startOffset !== "string" ||
        typeof endOffset !== "string"
      ) {
        return documentProgress();
      }
      const targetOffset: [string, string] = [startOffset, endOffset];
      return targetOffsetProgress(root, targetOffset) ?? documentProgress();
    }
  }
}

/** Replays target-specific curves captured in animation-runtime-dump.json. */
export default function ScrollLinkedStyleDriver() {
  useEffect(() => {
    let raf = 0;
    const originalStyles: OriginalStyles = new Map();
    const apply = () => {
      const activeProperties: ActiveProperties = new Map();
      const applications: StyleApplication[] = [];
      for (const site of scrollLinkedStyleSites) {
        try {
          if (!mediaMatches(site.media)) continue;
          const root: ParentNode | null = site.scope
            ? document.querySelector(site.scope)
            : document;
          const targets = root
            ? selectScopedCandidates(root, site.selector, site.selectorIndex, site.replay)
            : [];
          for (const target of targets) {
            if (!(target instanceof HTMLElement || target instanceof SVGElement)) {
              continue;
            }
            const progress = root
              ? siteProgress(
                  site.inputDomain,
                  site.progressSource ?? "document-progress",
                  site.offset,
                  root,
                )
              : documentProgress();
            noteActiveBandProperties(activeProperties, target, site.bands);
            applications.push({
              target,
              progress,
              bands: site.bands,
              units: site.units,
            });
          }
        } catch {
          // Selector syntax is validated upstream; keep one bad site isolated.
        }
      }
      // Restore only properties for which no currently enabled site has a
      // write. This keeps an inactive breakpoint site from erasing an active
      // site's value when both target the same element/property.
      restoreInactiveBandStyles(originalStyles, activeProperties);
      for (const application of applications) {
        rememberBandStyles(
          originalStyles,
          application.target,
          application.bands,
        );
        applyBandStyles(
          application.target.style,
          application.progress,
          application.bands,
          application.units,
        );
      }
    };
    const schedule = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        apply();
      });
    };
    apply();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("ui-clone-scroll", schedule as EventListener);
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("ui-clone-scroll", schedule as EventListener);
      window.removeEventListener("resize", schedule);
      cancelAnimationFrame(raf);
      restoreAllBandStyles(originalStyles);
    };
  }, []);
  return null;
}
''')

if isinstance(_ss, dict) and _ss.get("required"):
    _sites_out: list[dict[str, Any]] = []
    for _site in _ss.get("sites", []) or []:
        if not isinstance(_site, dict):
            continue
        if str(_site.get("progressSource") or "") in {
            "document", "document-progress", "target-offset"
        }:
            continue
        _wrapper_bands: dict[str, Any] = {}
        _has_scale = False
        for _t in _site.get("transforms", []) or []:
            if not isinstance(_t, dict):
                continue
            _prop = _t.get("property")
            if _prop not in _SCRUB_PROP_KEYS or _prop in _wrapper_bands:
                continue
            _inp = _parse_scrub_range(_t.get("input"))
            _outp = _parse_scrub_range(_t.get("output"))
            if not _inp or not _outp or len(_inp) != len(_outp):
                continue
            if not _normalized_scrub_input(_inp):
                continue
            if not _scrub_band_plausible(_prop, _outp):
                continue
            _wrapper_bands[_prop] = [_inp, _outp]
            if _prop.startswith("scale"):
                _has_scale = True
        if not _wrapper_bands:
            continue
        _offset = None
        if isinstance(_site.get("offset"), str):
            try:
                _parsed = json.loads(_site["offset"])
                if isinstance(_parsed, list) and len(_parsed) == 2:
                    _offset = _parsed
            except (json.JSONDecodeError, ValueError):
                _offset = None
        _entry = dict(_wrapper_bands)
        if _offset is not None:
            _entry["offset"] = _offset
        # The bundle's decompiled spring params are direct evidence of which
        # sites the ref sprung and how stiffly. Prefer them; the scale heuristic
        # below stays only as the fallback for sites captured without params
        # (scale-zoom backgrounds resolve scale through a useSpring hop).
        _site_spring = _site.get("spring")
        if isinstance(_site_spring, dict):
            _spring_cfg = {
                _k: _v
                for _k, _v in _site_spring.items()
                if _k in ("stiffness", "damping", "mass", "restDelta")
                and isinstance(_v, (int, float))  # noqa: UP038
                and not isinstance(_v, bool)
            }
            if _spring_cfg:
                _entry["spring"] = True
                _entry["springConfig"] = _spring_cfg
        if "spring" not in _entry and _has_scale:
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
            'import type { UseScrollOptions } from "framer-motion";\n'
            "export type ScrubBand = [number[], number[]];\n"
            "export interface SpringConfig {\n"
            "  stiffness?: number;\n"
            "  damping?: number;\n"
            "  mass?: number;\n"
            "  restDelta?: number;\n"
            "}\n"
            "export interface ScrubSite {\n"
            "  offset?: UseScrollOptions[\"offset\"];\n"
            "  scale?: ScrubBand;\n"
            "  scaleX?: ScrubBand;\n"
            "  scaleY?: ScrubBand;\n"
            "  opacity?: ScrubBand;\n"
            "  x?: ScrubBand;\n"
            "  y?: ScrubBand;\n"
            "  rotate?: ScrubBand;\n"
            "  width?: ScrubBand;\n"
            "  height?: ScrubBand;\n"
            "  borderRadius?: ScrubBand;\n"
            "  spring?: boolean;\n"
            "  springConfig?: SpringConfig;\n"
            "}\n\n"
            "export const scrollScrubSites: ScrubSite[] = "
            + json.dumps(_sites_out, ensure_ascii=False)
            + ";\n"
        ))
        _emit("ScrollScrub.tsx", '''"use client";

import { useRef } from "react";
import type { ReactNode } from "react";
import { motion, useScroll, useTransform, useSpring } from "framer-motion";
import type { UseScrollOptions } from "framer-motion";
import type { ScrubBand, SpringConfig } from "./scrollScrubSites";

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
  offset?: UseScrollOptions["offset"];
  scale?: ScrubBand;
  scaleX?: ScrubBand;
  scaleY?: ScrubBand;
  opacity?: ScrubBand;
  x?: ScrubBand;
  y?: ScrubBand;
  rotate?: ScrubBand;
  width?: ScrubBand;
  height?: ScrubBand;
  borderRadius?: ScrubBand;
  spring?: boolean;
  springConfig?: SpringConfig;
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
  width,
  height,
  borderRadius,
  spring = false,
  springConfig,
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
  const mvWidth = useTransform(scrollYProgress, ...id(width, 0));
  const mvHeight = useTransform(scrollYProgress, ...id(height, 0));
  const mvBorderRadius = useTransform(scrollYProgress, ...id(borderRadius, 0));

  // A site carrying decompiled spring params replays at the ref's own
  // stiffness/damping; these constants are only the fallback for sites
  // captured before the params were extracted.
  const sp = { stiffness: 120, damping: 30, ...(springConfig ?? {}) };
  const spScale = useSpring(mvScale, sp);
  const spScaleX = useSpring(mvScaleX, sp);
  const spScaleY = useSpring(mvScaleY, sp);
  const spOpacity = useSpring(mvOpacity, sp);
  const spX = useSpring(mvX, sp);
  const spY = useSpring(mvY, sp);
  const spRotate = useSpring(mvRotate, sp);

  const style: Record<string, unknown> = {};
  if (scale) style.scale = spring ? spScale : mvScale;
  if (scaleX) style.scaleX = spring ? spScaleX : mvScaleX;
  if (scaleY) style.scaleY = spring ? spScaleY : mvScaleY;
  // The legacy `spring` flag is inferred from "this site has a scale band", so
  // it may only smooth scale. Widening it to the other channels needs the ref's
  // own params as evidence, or an existing capture's replay would silently change.
  const springAll = spring && Boolean(springConfig);
  if (opacity) style.opacity = springAll ? spOpacity : mvOpacity;
  if (x) style.x = springAll ? spX : mvX;
  if (y) style.y = springAll ? spY : mvY;
  if (rotate) style.rotate = springAll ? spRotate : mvRotate;
  if (width) style.width = mvWidth;
  if (height) style.height = mvHeight;
  if (borderRadius) style.borderRadius = mvBorderRadius;

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
import type { UseScrollOptions } from "framer-motion";

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
  offset?: UseScrollOptions["offset"];
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


# ── WordRevealDriver.tsx (Fix 128) ──────────────────────────────────────────
# ScrollWordHighlight above re-splits a plain string, so it can only be used on
# text the agent re-authors. When the reference itself ships the split — every
# word already its own span carrying the dim class — scaffold-to-jsx transpiles
# those spans verbatim and there is nothing left to wrap. The result is a page
# whose word spans are all permanently dim because no code ever advances the
# reading head.
#
# This driver adopts the transpiled spans in place. It only toggles the two
# class names the reference stylesheet already defines (dim ↔ highlight), so
# every colour and transition value stays owned by the imported ref CSS.
def _css_blob() -> str:
    roots = [plan_path.parent, impl_dir / "src"]
    chunks: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.css"):
            if path.is_file():
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _css_defines_class(css: str, class_name: str) -> bool:
    # Look for a real selector token, not a substring inside another CSS-module class.
    return bool(_re.search(r"(?<![\\\w-])\." + _re.escape(class_name) + r"(?![\w-])", css))


def _explicit_highlight_class(effect: dict[str, Any]) -> str | None:
    for key in (
        "highlightClass",
        "highlightClassName",
        "activeClass",
        "activeClassName",
        "revealedClass",
        "revealedClassName",
    ):
        value = effect.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lstrip(".")
    selector = effect.get("highlightSelector")
    if isinstance(selector, str):
        selector = selector.strip()
        if selector.startswith(".") and not any(c in selector[1:] for c in " >+~,.#[]():*"):
            return selector[1:]
    return None


def _word_class_pair(effects: Any) -> tuple[str, str] | None:
    """Resolve (dimClass, highlightClass) from a per-word signatureEffect."""
    css = _css_blob()
    for effect in effects if isinstance(effects, list) else []:
        if not isinstance(effect, dict):
            continue
        if "per-word" not in str(effect.get("effectType") or "").lower():
            continue
        selector = str(effect.get("wordSelector") or "").strip()
        # Only a bare single-class selector is safe to toggle blindly: anything
        # with a combinator, a second class, or a pseudo needs a real matcher.
        if not selector.startswith("."):
            continue
        dim = selector[1:]
        if not dim or any(c in dim for c in " >+~,.#[]():*"):
            continue
        explicit = _explicit_highlight_class(effect)
        if explicit:
            return dim, explicit
        for needle, replacement in (
            ("dimmed", "highlighted"),
            ("dim", "highlight"),
            ("inactive", "active"),
        ):
            if needle in dim:
                highlight = dim.replace(needle, replacement)
                if _css_defines_class(css, highlight):
                    return dim, highlight
    return None


_word_classes = _word_class_pair(_effects)
if _word_classes:
    _dim_class, _highlight_class = _word_classes
    _emit("WordRevealDriver.tsx", f'''"use client";

import {{ useEffect }} from "react";

/**
 * Deterministically emitted by skills/visual-debug/scripts/emit-scroll-helpers.sh
 * from generation-plan.signatureEffects (per-word-split). The reference ships
 * the text pre-split — one span per word, each carrying the dim class — and
 * advances a scroll "reading head" that swaps each word to the highlight class
 * as it passes. Both class names live in the imported reference stylesheet, so
 * this driver never owns a colour, an opacity, or a transition; it only decides
 * how many words are past the head. Mount it once from the layout (page.tsx is
 * regenerated verbatim by scaffold-to-jsx.sh). Do not hand-edit — re-run the
 * emitter.
 */
const DIM_CLASS = {json.dumps(_dim_class)};
const HIGHLIGHT_CLASS = {json.dumps(_highlight_class)};

interface WordGroup {{
  words: HTMLElement[];
  track: HTMLElement;
  pinned: boolean;
}}

function collectGroups(): WordGroup[] {{
  // Stamp the spans that were transpiled without an id: once a word is revealed
  // it no longer carries DIM_CLASS, so data-word-id is the only stable handle.
  document.querySelectorAll<HTMLElement>(`.${{DIM_CLASS}}:not([data-word-id])`).forEach((word, i) => {{
    word.dataset.wordId = `driver-word-${{i}}`;
  }});

  // The transpiler wraps every word in its own anonymous span, so parentElement
  // is not a usable group key — climb to the owning paragraph instead.
  const byParent = new Map<HTMLElement, HTMLElement[]>();
  document.querySelectorAll<HTMLElement>("[data-word-id]").forEach((word) => {{
    const parent = word.closest("p") ?? word.parentElement;
    if (!parent) return;
    const bucket = byParent.get(parent);
    if (bucket) bucket.push(word);
    else byParent.set(parent, [word]);
  }});

  const groups: WordGroup[] = [];
  byParent.forEach((words, parent) => {{
    let sticky: HTMLElement | null = null;
    for (let node: HTMLElement | null = parent; node; node = node.parentElement) {{
      if (window.getComputedStyle(node).position === "sticky") {{
        sticky = node;
        break;
      }}
    }}
    groups.push({{
      words,
      track: sticky?.parentElement ?? parent,
      pinned: Boolean(sticky?.parentElement),
    }});
  }});
  return groups;
}}

function progressFor(group: WordGroup, viewportHeight: number): number {{
  const rect = group.track.getBoundingClientRect();
  // A pinned block scrubs across its wrapper's spare scroll distance; a static
  // block scrubs across its own viewport travel, starting as it enters the
  // lower band and finishing once it has cleared the top.
  const raw = group.pinned
    ? -rect.top / Math.max(1, rect.height - viewportHeight)
    : (viewportHeight * 0.85 - rect.top) / Math.max(1, rect.height + viewportHeight * 0.35);
  return Math.min(1, Math.max(0, raw));
}}

export default function WordRevealDriver() {{
  useEffect(() => {{
    let groups = collectGroups();
    if (!groups.length) return;

    let frame = 0;
    const apply = () => {{
      frame = 0;
      const viewportHeight = window.innerHeight;
      for (const group of groups) {{
        const active = Math.round(progressFor(group, viewportHeight) * group.words.length);
        group.words.forEach((word, index) => {{
          const on = index < active;
          word.classList.toggle(HIGHLIGHT_CLASS, on);
          word.classList.toggle(DIM_CLASS, !on);
        }});
      }}
    }};
    const schedule = () => {{
      if (!frame) frame = window.requestAnimationFrame(apply);
    }};
    const remeasure = () => {{
      groups = collectGroups();
      schedule();
    }};

    apply();
    window.addEventListener("scroll", schedule, {{ passive: true }});
    window.addEventListener("resize", remeasure);
    return () => {{
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", remeasure);
      if (frame) window.cancelAnimationFrame(frame);
    }};
  }}, []);

  return null;
}}
''')


# ── ScrollLatchDriver.tsx (discrete scroll states, from scrollLatch.sites) ───
# The capture's return sweep proved these do not reverse, so they are states
# and not curves. Interpolating them across a progress band is what renders
# every state permanently half-applied.
_latch_plan = plan.get("scrollLatch")
_latch_raw = _latch_plan.get("sites") if isinstance(_latch_plan, dict) else None
_latch_sites: list[dict[str, Any]] = []
for _site in _latch_raw if isinstance(_latch_raw, list) else []:
    if not isinstance(_site, dict):
        continue
    _selector = _site.get("selector")
    _end_state = _site.get("endState")
    _progress = _site.get("progress")
    if not isinstance(_selector, str) or not _selector.strip():
        continue
    if not isinstance(_end_state, dict) or not _end_state:
        continue
    if not _is_number(_progress):
        continue
    _declarations = {
        _re.sub(r"(?<!^)(?=[A-Z])", "-", str(_prop)).lower(): str(_value)
        for _prop, _value in _end_state.items()
        if _value is not None
    }
    if not _declarations:
        continue
    _latch_selector_index = _site.get("selectorIndex")
    _latch_sites.append(
        {
            "selector": _selector,
            "selectorIndex": (
                _latch_selector_index
                if isinstance(_latch_selector_index, int) and _latch_selector_index >= 0
                else 0
            ),
            "progress": float(cast(float, _progress)),
            "endState": _declarations,
        }
    )

if _latch_sites:
    _emit(
        "ScrollLatchDriver.tsx",
        '''"use client";

import {{ useEffect }} from "react";

/**
 * Deterministically emitted by skills/visual-debug/scripts/emit-scroll-helpers.sh
 * from generation-plan.json scrollLatch.sites — states the capture's return
 * sweep proved do not reverse. Each endState is applied once scroll progress
 * passes its fraction and then left alone; these must never be interpolated.
 * The threshold is a fraction of the live scroll range, not a captured pixel
 * offset, so it survives a document-height change.
 * Do not hand-edit — re-run the emitter to refresh.
 */
const LATCH_SITES: Array<{{
  selector: string;
  selectorIndex: number;
  progress: number;
  endState: Record<string, string>;
}}> = {sites};

export default function ScrollLatchDriver() {{
  useEffect(() => {{
    let raf = 0;
    const apply = () => {{
      const range = Math.max(
        document.documentElement.scrollHeight - window.innerHeight,
        1,
      );
      const progress = window.scrollY / range;
      for (const site of LATCH_SITES) {{
        if (progress < site.progress) continue;
        const node = document.querySelectorAll(site.selector)[site.selectorIndex];
        if (!(node instanceof HTMLElement)) continue;
        for (const [property, value] of Object.entries(site.endState)) {{
          node.style.setProperty(property, value, "important");
        }}
      }}
    }};
    const schedule = () => {{
      if (raf) return;
      raf = requestAnimationFrame(() => {{
        raf = 0;
        apply();
      }});
    }};
    apply();
    window.addEventListener("scroll", schedule, {{ passive: true }});
    return () => {{
      window.removeEventListener("scroll", schedule);
      if (raf) cancelAnimationFrame(raf);
    }};
  }}, []);
  return null;
}}
'''.format(sites=json.dumps(_latch_sites, indent=2)),  # noqa: UP032
    )


if emitted:
    print(f"✓ emit-scroll-helpers: wrote {', '.join(emitted)} → {lib}")
else:
    print("▸ emit-scroll-helpers: no scroll helpers required — nothing emitted")
