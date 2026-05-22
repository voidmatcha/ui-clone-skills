#!/usr/bin/env bash
# generation-plan.sh — produce a deterministic generation-plan.json from
# detection artifacts. Bridges the Detection → Generation gap that opened
#
# Input:  tmp/ref/<component>/ — must contain Phase 1-5 artifacts
# Output: tmp/ref/<component>/generation-plan.json
#
# The plan is meant to be the SINGLE SOURCE OF TRUTH for Phase 6
# generation: component list, library installs, sticky/hidden strategy,
# initial animation state, asset-substitution mode. Claude Code MAY
# additionally invoke the `generation-planner` sub-agent to enrich the
# plan with qualitative judgments. Codex consumes the deterministic
# plan directly.
#
# Usage: generation-plan.sh <ref-dir>
set -euo pipefail

REF_DIR="${1:-}"
if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: $0 <ref-dir>" >&2
  exit 2
fi

OUT="$REF_DIR/generation-plan.json"

python3 - "$REF_DIR" "$OUT" <<'PY'
# Python 3.9 compat for PEP 604 unions used below — defer
# annotation evaluation so `X | Y` is parsed as a string.
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
out_path = Path(sys.argv[2])


def load(name: str, default):
    p = ref_dir / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return default


section_map = load("section-map.json", [])
sticky = load("sticky-elements.json", [])
hidden = load("hidden-elements.json", [])
mobile_swap = load("mobile-swap.json", {"mobile_swap_sections": []})
anim_init = load("animation-init-styles.json", [])
external_sdks = load("external-sdks.json", {})
bundle_map = load("bundle-map.json", {})
transition_spec = load("transition-spec.json", {})
scroll_engine = load("scroll-engine.json", {})
paid_features = load("paid-features.json", {})
asset_sub = load("asset-substitution.json", {})
font_parity = load("font-parity.json", {})
canvas_webgl = load("canvas-webgl-detection.json", {})


# ── Component list ──────────────────────────────────────────────
# Each top-level section (post jumbo-main descent) becomes a component.
# section-map.json has two known shapes:
#  v0.2.x: flat array [{idx, tag, className, selector, rect}, ...]
#  newer:  wrapped object {totalCount, sections: [{id, tag, cls, top, height}, ...]}
if isinstance(section_map, dict):
    sections = section_map.get("sections", [])
elif isinstance(section_map, list):
    sections = section_map
else:
    sections = []
if not isinstance(sections, list):
    sections = []

# Build a quick lookup of initial-state values by selector.
init_by_selector: dict[str, dict] = {}
if isinstance(anim_init, list):
    for entry in anim_init:
        if not isinstance(entry, dict):
            continue
        sel = entry.get("selector")
        if sel:
            init_by_selector[sel] = {
                "inlineOpacity": entry.get("inlineOpacity", ""),
                "inlineTransform": entry.get("inlineTransform", ""),
            }

components: list[dict] = []
for s in sections:
    if not isinstance(s, dict):
        continue
    sid = s.get("id") or s.get("name") or s.get("selector") or f"section-{len(components)}"
    tag = s.get("tag", "section")
    cls = s.get("cls") or s.get("className") or ""
    # Naming: PascalCase from id, removing leading dashes.
    raw = sid.replace("section-", "Section") if sid.startswith("section-") else sid
    name = "".join(part.capitalize() or "" for part in raw.replace("-", " ").replace("_", " ").split())
    if not name:
        name = f"Section{len(components)}"
    selector = f"{tag}.{cls}".rstrip(".") if cls else tag
    init = init_by_selector.get(selector) or init_by_selector.get(f".{cls}") or {}
    components.append({
        "name": name,
        "matchedSection": sid,
        "tag": tag,
        "selector": selector,
        "path": f"components/sections/{name}.tsx",
        "initialState": init,
    })


# ── Library mirroring ────────────────────────────────────────────
# Cross reference external-sdks + bundle-map to detect Lenis / GSAP /
# Framer Motion / Anime.js / Auto-Animate.
detected_libs: dict[str, dict] = {}


def detect_in(blob: dict | list, marker: str, install: str, rationale: str):
    text = json.dumps(blob).lower()
    if marker.lower() in text:
        detected_libs.setdefault(install, {"rationale": rationale})


detect_in(external_sdks, "lenis", "lenis", "Smooth-scroll library detected in external-sdks.json")
detect_in(external_sdks, "gsap", "gsap", "GSAP detected — install gsap + gsap/ScrollTrigger as needed")
detect_in(external_sdks, "framer", "framer-motion", "Framer Motion detected")
detect_in(external_sdks, "anime", "animejs", "Anime.js detected")
detect_in(external_sdks, "auto-animate", "@formkit/auto-animate", "Auto-Animate detected")
detect_in(bundle_map, "lenis", "lenis", "lenis chunk found in bundle-map")
detect_in(bundle_map, "scrolltrigger", "gsap", "GSAP ScrollTrigger chunk in bundle-map")
detect_in(bundle_map, "framer-motion", "framer-motion", "framer-motion in bundle-map")
detect_in(scroll_engine, "lenis", "lenis", "scroll-engine declares Lenis")
detect_in(scroll_engine, "scrollsmoother", "gsap", "ScrollSmoother (GSAP) detected")


# ── Sticky strategy ──────────────────────────────────────────────
sticky_plan = []
# Detect GSAP ScrollTrigger.pin in bundle-map / transition-spec — when present,
# the sticky element is likely a pin target rather than CSS sticky.
bundle_text = json.dumps(bundle_map).lower() + json.dumps(transition_spec).lower()
has_scroll_trigger_pin = (
    "scrolltrigger" in bundle_text and ("pin" in bundle_text or "pin:" in bundle_text)
)
if isinstance(sticky, list):
    for entry in sticky:
        if not isinstance(entry, dict):
            continue
        position = entry.get("position", "")
        # Mechanism: gsap-pin if ScrollTrigger.pin detected AND this element
        # has no CSS position:sticky/fixed (sub-agent can override). Default
        # to css-sticky/css-fixed mirror.
        if has_scroll_trigger_pin and position not in ("sticky", "fixed"):
            mechanism = "gsap-pin"
        elif position == "sticky":
            mechanism = "css-sticky"
        elif position == "fixed":
            mechanism = "css-fixed"
        else:
            mechanism = "css-sticky"
        sticky_plan.append({
            "selector": f"{entry.get('tag', '')}.{entry.get('cls', '')}".rstrip("."),
            "position": position,
            "top": entry.get("top"),
            "zIndex": entry.get("zIndex"),
            "renderAt": "App",  # single render at App/layout level
            "mechanism": mechanism,
            "note": "Render ONCE at App/layout level; do not duplicate per section.",
        })


# ── Hidden / variant ─────────────────────────────────────────────
hidden_plan = []
if isinstance(hidden, list):
    for entry in hidden:
        if not isinstance(entry, dict):
            continue
        reason = entry.get("reason", "")
        hidden_plan.append({
            "selector": entry.get("selector"),
            "reason": reason,
            "initialMode": (
                "responsive-hidden" if "display:none" in reason
                else "opacity-zero" if "opacity:0" in reason
                else "visibility-hidden" if "visibility" in reason
                else "preserve"
            ),
            "note": "Preserve initial state. Do NOT delete — many are entry-animation targets.",
        })


# ── Mobile / desktop dual variants ───────────────────────────────
mobile_swap_plan = []
if isinstance(mobile_swap, dict):
    for s in mobile_swap.get("mobile_swap_sections", []) or []:
        mobile_swap_plan.append({
            "section": s,
            "strategy": "render both with responsive Tailwind gates (md:hidden / hidden md:block)",
        })


# ── Architecture layers ──────────────────────────────────────────
# Heuristics for when to create each layer.
section_count = len(components)
arch_layers = {
    "tokens": section_count >= 3,
    "dsComponents": section_count >= 6,
    "constants": section_count >= 4,
    "libTransitions": bool(transition_spec.get("transitions")) if isinstance(transition_spec, dict) else False,
}


# ── Scroll wiring ────────────────────────────────────────────────
has_smooth_scroll = (
    "lenis" in detected_libs
    or "scrollsmoother" in json.dumps(scroll_engine).lower()
    or json.dumps(scroll_engine).lower().count("smooth") >= 1
)
smooth_scroll_plan = {
    "required": has_smooth_scroll,
    "wrapper": "lib/SmoothScroll.tsx" if has_smooth_scroll else None,
    "library": "lenis" if "lenis" in detected_libs else (
        "gsap-ScrollSmoother" if "gsap" in detected_libs and "scrollsmoother" in json.dumps(scroll_engine).lower() else None
    ),
}
scroll_listener_plan = {
    "required": not has_smooth_scroll and bool(transition_spec.get("transitions") if isinstance(transition_spec, dict) else False),
    "wrapper": "lib/ScrollListener.tsx" if (not has_smooth_scroll and transition_spec) else None,
    "approach": "RAF + getBoundingClientRect, single passive listener, write transforms via refs",
}


# ── Intro animation ──────────────────────────────────────────────
intro_animation_required = (
    sum(1 for v in init_by_selector.values() if v.get("inlineOpacity") in ("0", "0.0") or v.get("inlineTransform") not in ("", "none"))
    >= 1
)
intro_plan = {
    "required": intro_animation_required,
    "wrapper": "components/ui/IntroAnimation.tsx" if intro_animation_required else None,
    "note": "Coordinates initial visibility resets + post-mount/scroll triggers.",
}


# ── Asset substitution + validation (Common cheat pattern) ───────────
# Refuse `replacement: "emoji-or-gradient"` or similar placeholder patterns —
# these wreck visual fidelity. Image substitution requires a CONCRETE
# substitution target (alternative CDN URL, royalty-free service path, etc.)
# AND evidence the original couldn't be downloaded (HTTP failure log).
BANNED_REPLACEMENTS = {"emoji-or-gradient", "emoji", "gradient", "placeholder", "stub"}
sub_violations: list = []
if isinstance(asset_sub, dict):
    for img in asset_sub.get("images", []) or []:
        if not isinstance(img, dict):
            continue
        repl = (img.get("replacement") or "").lower().strip()
        if repl in BANNED_REPLACEMENTS:
            sub_violations.append({
                "originalSrc": img.get("originalSrc"),
                "replacement": img.get("replacement"),
                "reason": "Emoji/gradient/placeholder is not a valid image substitution. Download the real image OR pick a concrete CC0 substitute URL.",
            })
asset_sub_plan = {
    "fontSubstitution": bool(asset_sub.get("fonts") if isinstance(asset_sub, dict) else False),
    "structuralOnlySections": (asset_sub.get("structuralOnlySections") if isinstance(asset_sub, dict) else []) or [],
    "templateMode": (
        isinstance(asset_sub, dict)
        and "*" in (asset_sub.get("structuralOnlySections") or [])
    ),
    "violations": sub_violations,
}


# ── Canvas / WebGL ───────────────────────────────────────────────
canvas_plan = {
    "hasCanvas": bool(canvas_webgl.get("hasCanvas")) if isinstance(canvas_webgl, dict) else False,
    "hasWebGL": bool(canvas_webgl.get("hasWebGL")) if isinstance(canvas_webgl, dict) else False,
    "renderType": canvas_webgl.get("primaryRenderType", "dom") if isinstance(canvas_webgl, dict) else "dom",
}


# ── Paid features ────────────────────────────────────────────────
paid_plan = {}
if isinstance(paid_features, list):
    paid_plan = {"findings": paid_features}
elif isinstance(paid_features, dict):
    paid_plan = paid_features


# ── Tokens / ds-components / signature effects — stubs ──────────
# These are stub fields that the Claude Code `generation-planner` sub-agent
# fills with semantic names. Codex agents inline-enrich them per
# .codex-plugin/plugin.json defaultPrompt. Empty values mean "still needs
# enrichment" — not "no such pattern in ref".
tokens_stub: dict = {
    "colors": {},
    "spacing": {},
    "typography": {},
    "radius": {},
    "shadows": {},
    "_note": "Sub-agent (Claude) or Codex inline-enrichment fills semantic names from css/variables.txt + styles.json.",
}
ds_components_required: list = []
# Plan emits signatureEffects as null when generation-planner hasn't run yet;
# the enrichment pass (Claude sub-agent or Codex inline) replaces null with [].
signature_effects = None


# ── Assemble plan ────────────────────────────────────────────────
plan = {
    "schemaVersion": 1,
    "component": ref_dir.name,
    "componentList": components,
    "dsComponentsRequired": ds_components_required,
    "tokens": tokens_stub,
    "libraries": {
        "required": sorted(detected_libs.keys()),
        "rationale": detected_libs,
    },
    "stickyStrategy": sticky_plan,
    "hiddenElements": hidden_plan,
    "mobileSwap": mobile_swap_plan,
    "architectureLayers": arch_layers,
    "smoothScroll": smooth_scroll_plan,
    "scrollListener": scroll_listener_plan,
    "introAnimation": intro_plan,
    "signatureEffects": signature_effects,
    "assetSubstitution": asset_sub_plan,
    "canvas": canvas_plan,
    "paidFeatures": paid_plan,
    "guidance": {
        "rule": "Phase 6 (generation) MUST follow this plan. Each top-level array is a contract: missing component / library / sticky / hidden entry = generation incomplete.",
        "skipPolicy": "If a layer is intentionally skipped, record artifact-backed rationale in implementation notes — 'looks fine' / 'small page' is not enough.",
    },
}

out_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
print(f"✓ generation-plan.json written → {out_path}")
print(f"  components: {len(components)} | libraries: {sorted(detected_libs.keys())}")
print(f"  sticky: {len(sticky_plan)} | hidden: {len(hidden_plan)} | mobile-swap: {len(mobile_swap_plan)}")
print(f"  smoothScroll: {smooth_scroll_plan['required']} | introAnimation: {intro_plan['required']}")
print(f"  arch layers: tokens={arch_layers['tokens']} ds-components={arch_layers['dsComponents']} constants={arch_layers['constants']}")
PY
