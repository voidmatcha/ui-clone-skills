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
import hashlib
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

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
bundle_extraction = load("bundle-extraction.json", {})
paid_features = load("paid-features.json", {})
asset_sub = load("asset-substitution.json", {})
font_parity = load("font-parity.json", {})
canvas_webgl = load("canvas-webgl-detection.json", {})


CSS_MODULE_CLASS_RE = re.compile(r"\b[A-Za-z][\w-]*__[A-Za-z0-9_-]{4,}\b")
CSS_MODULE_FORENSIC_MIN_SIGNATURES = 10
CSS_MODULE_STRONG_SIGNATURES_WITH_MISSING_CSS = 25
CSS_FORENSIC_MIN_BYTES = 10_000
CSS_DOWNLOAD_MAX_BYTES = 10 * 1024 * 1024
CSS_DOWNLOAD_TIMEOUT_SECONDS = 20


def walk_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_values(child)
    elif isinstance(value, str):
        yield value


def css_bytes_and_files() -> tuple[int, list[str]]:
    css_dir = ref_dir / "css"
    if not css_dir.is_dir():
        return 0, []
    files = sorted(p for p in css_dir.glob("*.css") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    return total, [str(p.relative_to(ref_dir)) for p in files]


def parse_jsonish(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def stylesheet_urls_from_artifact(value) -> list[str]:
    value = parse_jsonish(value)
    urls: list[str] = []
    candidates = []
    if isinstance(value, dict):
        candidates.append(value)
        head = parse_jsonish(value.get("head"))
        if isinstance(head, dict):
            candidates.append(head)
    for artifact in candidates:
        links = artifact.get("links")
        if not isinstance(links, list):
            continue
        for link in links:
            if not isinstance(link, dict):
                continue
            rel = str(link.get("rel") or "").lower()
            href = str(link.get("href") or "").strip()
            if href and "stylesheet" in rel:
                urls.append(href)
    # Preserve order while de-duplicating.
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def stylesheet_url_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http" and os.environ.get("UI_CLONE_CSS_DOWNLOAD_ALLOW_HTTP") == "1":
        return parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme == "file" and os.environ.get("UI_CLONE_CSS_DOWNLOAD_ALLOW_FILE") == "1":
        return True
    return False


def css_filename_for_url(url: str, used: set[str]) -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name
    if not name or not name.lower().endswith(".css"):
        name = f"{hashlib.sha256(url.encode()).hexdigest()[:12]}.css"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if name not in used:
        used.add(name)
        return name
    stem = name[:-4] if name.lower().endswith(".css") else name
    suffix = hashlib.sha256(url.encode()).hexdigest()[:8]
    deduped = f"{stem}-{suffix}.css"
    used.add(deduped)
    return deduped


def recover_linked_css_artifacts() -> None:
    """Download stylesheet links discovered during head extraction.

    Step 2.5 says CSS download is mandatory, but some host runs reach
    generation-plan with only head.json/extracted.json stylesheet URLs. Recover
    those CSS artifacts here before deciding whether forensic preservation is
    possible; otherwise CSS-module-heavy pages get misrouted to freehand rebuilds.
    """
    urls = stylesheet_urls_from_artifact(load("head.json", {}))
    urls.extend(stylesheet_urls_from_artifact(load("extracted.json", {})))
    seen: set[str] = set()
    urls = [u for u in urls if not (u in seen or seen.add(u))]
    if not urls:
        return

    css_dir = ref_dir / "css"
    css_dir.mkdir(parents=True, exist_ok=True)
    used_names = {p.name for p in css_dir.glob("*.css")}
    log_rows: list[dict] = []
    for url in urls:
        row = {"url": url, "status": "skipped", "file": None, "bytes": 0}
        if not stylesheet_url_allowed(url):
            row["error"] = "unsupported-or-unsafe-url-scheme"
            log_rows.append(row)
            continue
        filename = css_filename_for_url(url, used_names)
        target = css_dir / filename
        row["file"] = str(target.relative_to(ref_dir))
        if target.is_file() and target.stat().st_size > 500:
            row["status"] = "exists"
            row["bytes"] = target.stat().st_size
            log_rows.append(row)
            continue
        try:
            request_or_url = (
                Request(url, headers={"User-Agent": "ui-clone-skills/forensic-css"})
                if urlparse(url).scheme in {"http", "https"}
                else url
            )
            with urlopen(request_or_url, timeout=CSS_DOWNLOAD_TIMEOUT_SECONDS) as resp:
                data = resp.read(CSS_DOWNLOAD_MAX_BYTES + 1)
            if len(data) > CSS_DOWNLOAD_MAX_BYTES:
                row["status"] = "failed"
                row["error"] = "css-file-too-large"
            elif len(data) < 100 or b"{" not in data:
                row["status"] = "failed"
                row["error"] = "not-css-or-empty-response"
            else:
                target.write_bytes(data)
                row["status"] = "downloaded"
                row["bytes"] = len(data)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            row["status"] = "failed"
            row["error"] = str(exc)[:240]
        log_rows.append(row)
    try:
        (css_dir / "download-log.json").write_text(
            json.dumps(log_rows, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        pass


def class_signature_count(*artifacts, include_css: bool = True) -> int:
    """Distinct CSS-module className tokens.

    With include_css=True (default) it unions DOM-artifact tokens with every
    class DEFINED in the ref css/*.css files — the right signal for deciding
    whether a site is CSS-module-heavy (forensic_required).

    With include_css=False it counts only tokens that appear on captured DOM
    elements. That is the reachable ceiling a faithful clone can preserve in
    JSX: stylesheets define far more classes (pseudo/state/unused variants) than
    ever land on rendered elements, so the forensic *gate* threshold must be a
    fraction of THIS count, not of the inflated CSS-definition count — otherwise
    the gate is mathematically unreachable (realfood: 136 DOM classes but 599
    CSS-defined, so 25%*599=149 > 136 blocks every clone).
    """
    found: set[str] = set()
    for artifact in artifacts:
        for text in walk_values(artifact):
            found.update(CSS_MODULE_CLASS_RE.findall(text))
    if include_css:
        css_dir = ref_dir / "css"
        if css_dir.is_dir():
            for css_file in css_dir.glob("*.css"):
                try:
                    found.update(CSS_MODULE_CLASS_RE.findall(css_file.read_text(errors="ignore")))
                except OSError:
                    continue
    return len(found)


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

recover_linked_css_artifacts()
css_bytes, css_files = css_bytes_and_files()
_dom_artifacts = (section_map, load("structure.json", {}), load("dom-scaffold.json", {}))
css_module_signature_count = class_signature_count(*_dom_artifacts)
dom_class_signature_count = class_signature_count(*_dom_artifacts, include_css=False)
css_artifacts_complete = bool(css_files) and css_bytes >= CSS_FORENSIC_MIN_BYTES
css_artifacts_missing = css_module_signature_count >= CSS_MODULE_FORENSIC_MIN_SIGNATURES and not css_artifacts_complete
forensic_required = css_module_signature_count >= CSS_MODULE_FORENSIC_MIN_SIGNATURES and (
    css_artifacts_complete
    or css_module_signature_count >= CSS_MODULE_STRONG_SIGNATURES_WITH_MISSING_CSS
)
css_artifact_status = (
    "present"
    if css_artifacts_complete
    else "missing"
    if css_artifacts_missing
    else "not-applicable"
)
forensic_rules = [
    "copy ref css/*.css into impl/src/ref-css and import them before overrides",
    "translate dom-scaffold tree to JSX instead of freehanding new layout",
    "preserve original CSS-module className tokens",
    "preserve visible DOM hierarchy, media elements, and asset paths",
    "do not load reference JS bundles, proxy upstream HTML, or use screenshots as assets",
    "add transitions as local React/CSS/runtime controllers on top of the preserved scaffold",
]
if forensic_required and css_artifact_status == "missing":
    forensic_rules.insert(
        0,
        "CSS-module signatures are strong but ref css artifacts are missing/incomplete; "
        "rerun CSS capture or recover bundle CSS before generation and do not use "
        "standard-react-rebuild as a fallback",
    )
forensic_preservation = {
    "required": forensic_required,
    "strategy": "ref-derived-jsx-with-local-css" if forensic_required else "standard-react-rebuild",
    "classSignatureCount": css_module_signature_count,
    "domClassSignatureCount": dom_class_signature_count,
    "cssBytes": css_bytes,
    "cssFiles": css_files,
    "cssArtifactStatus": css_artifact_status,
    "missingCssArtifacts": forensic_required and css_artifact_status == "missing",
    "blockedUntilCssArtifacts": forensic_required and css_artifact_status == "missing",
    "copyCssTo": "src/ref-css" if forensic_required else None,
    "rules": forensic_rules,
}

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
def sticky_containing_block(css_class):
    """For a CSS-module class that is position:sticky in the captured DOM, find
    its nearest ancestor with position:relative — the element that bounds the
    sticky's scroll range. Returns {selector, height, position} or None.

    Without this the generator renders the sticky flat at App level, so its
    containing block becomes the whole page body and it pins for the entire
    scroll instead of releasing at the end of its section (the realfood
    "sticky never releases" bug). class_signature already proves the structure
    preserves section->sticky nesting; we just surface the wrapper to codegen.
    """
    structure = load("structure.json", {})
    target = (css_class or "").strip()
    if not target:
        return None
    result = {}

    def walk(node, ancestors):
        if not isinstance(node, dict):
            return False
        cls = node.get("class") or ""
        pos = (node.get("styles") or {}).get("position")
        if target in cls and pos == "sticky":
            for anc in reversed(ancestors):
                apos = (anc.get("styles") or {}).get("position")
                if apos == "relative":
                    acls = (anc.get("class") or "").split()
                    sel = f"{anc.get('tag','')}." + ".".join(acls) if acls else anc.get("tag", "")
                    result.update({
                        "selector": sel.rstrip("."),
                        "height": (anc.get("styles") or {}).get("height"),
                        "position": "relative",
                    })
                    return True
            return True  # sticky found but no relative ancestor
        for child in node.get("children") or []:
            if walk(child, ancestors + [node]):
                return True
        return False

    walk(structure, [])
    return result or None


sticky_plan = []
# Detect GSAP ScrollTrigger.pin in bundle-map / transition-spec — when present,
# the sticky element is likely a pin target rather than CSS sticky.
bundle_text = json.dumps(bundle_map).lower() + json.dumps(transition_spec).lower()
has_scroll_trigger_pin = (
    "scrolltrigger" in bundle_text and ("pin" in bundle_text or "pin:" in bundle_text)
)
# sticky-elements.json is `{"elements": [...]}` (not a bare list); reading it
# as a list left stickyStrategy empty, so the agent improvised sticky onto
# whole sections instead of mirroring the real fixed/sticky elements.
sticky_entries = sticky.get("elements", []) if isinstance(sticky, dict) else sticky
if isinstance(sticky_entries, list):
    for entry in sticky_entries:
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
        css_class = entry.get("className") or entry.get("cls", "")
        containing_block = (
            sticky_containing_block(css_class) if mechanism == "css-sticky" else None
        )
        # css-sticky must render INSIDE its relative containing block (the section
        # whose height bounds the pin), not flat at App level — else it pins to
        # the page body and never releases. css-fixed / gsap-pin stay at App.
        render_at = containing_block["selector"] if containing_block else "App"
        sticky_plan.append({
            "selector": f"{entry.get('tag', '')}.{css_class}".rstrip("."),
            "position": position,
            "top": entry.get("top") if entry.get("top") is not None else entry.get("stickyTop"),
            "zIndex": entry.get("zIndex"),
            "renderAt": render_at,
            "containingBlock": containing_block,
            "mechanism": mechanism,
            "note": (
                "Wrap this sticky inside its containingBlock (position:relative + "
                "that height) so it releases at the section end; do not render it "
                "flat at App level."
                if containing_block
                else "Render ONCE at App/layout level; do not duplicate per section."
            ),
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
# Require an explicit smooth-scroll engine/library — NOT a bare "smooth"
# substring match, which previously matched the `smoothScroll` KEY even when its
# value was false ({"engine":"native","smoothScroll":false} wrongly injected
# Lenis). Codex review regression.
_se = scroll_engine if isinstance(scroll_engine, dict) else {}
_se_engine = (_se.get("engine") or "").strip().lower()
has_smooth_scroll = (
    "lenis" in detected_libs
    or _se_engine in ("lenis", "locomotive", "scrollsmoother")
    or ("gsap" in detected_libs and "scrollsmoother" in json.dumps(_se).lower())
)
# Thread the site's real Lenis constructor options (from scroll-engine.json)
# into the plan so the generated SmoothScroll.tsx mirrors the reference feel
# instead of falling back to Lenis library defaults. Only pass through known
# Lenis keys; absent options → empty dict (generator uses Lenis defaults).
_LENIS_OPTION_KEYS = (
    "lerp", "duration", "easing", "smoothWheel", "smoothTouch",
    "wheelMultiplier", "touchMultiplier", "orientation", "gestureOrientation",
    "direction", "infinite", "syncTouch",
)
smooth_scroll_config: dict = {}
if has_smooth_scroll:
    raw_opts = scroll_engine.get("options") if isinstance(scroll_engine, dict) else None
    if isinstance(raw_opts, dict):
        smooth_scroll_config = {
            k: raw_opts[k] for k in _LENIS_OPTION_KEYS if k in raw_opts
        }
smooth_scroll_plan = {
    "required": has_smooth_scroll,
    "wrapper": "lib/SmoothScroll.tsx" if has_smooth_scroll else None,
    "library": "lenis" if "lenis" in detected_libs else (
        "gsap-ScrollSmoother" if "gsap" in detected_libs and "scrollsmoother" in json.dumps(scroll_engine).lower() else None
    ),
    "config": smooth_scroll_config,
}
scroll_listener_plan = {
    "required": not has_smooth_scroll and bool(transition_spec.get("transitions") if isinstance(transition_spec, dict) else False),
    "wrapper": "lib/ScrollListener.tsx" if (not has_smooth_scroll and transition_spec) else None,
    "approach": "RAF + getBoundingClientRect, single passive listener, write transforms via refs",
}


# ── Scroll-driven reveals (Framer useScroll / scrollYProgress) ───────
# scroll-engine.json's scrollDriven block records the library + hooks the site
# uses to map scroll progress onto opacity/transform (the primary reveal
# mechanism on Framer-Motion sites). Surface it as a contract so the generator
# wires real scrollYProgress→useTransform reveals instead of treating these as
# plain load/intersection fades.
_scroll_driven_raw = scroll_engine.get("scrollDriven") if isinstance(scroll_engine, dict) else None
if isinstance(_scroll_driven_raw, dict) and _scroll_driven_raw.get("hooks"):
    scroll_driven_plan = {
        "required": True,
        "library": _scroll_driven_raw.get("library"),
        "hooks": list(_scroll_driven_raw.get("hooks") or []),
        "evidence": _scroll_driven_raw.get("evidence", ""),
        "note": (
            "Map section scroll progress onto opacity/transform via "
            "scrollYProgress + useTransform. Under smooth-scroll (see "
            "smoothScroll) drive progress from the Lenis scroll source "
            "(ReactLenis root or RAF+getBoundingClientRect per "
            "component-generation rule 14) — never a raw window 'scroll' listener."
        ),
    }
elif "framer-motion" in detected_libs:
    # The decode step did not populate scroll-engine.scrollDriven, but
    # framer-motion IS detected — infer the scroll-driven reveal contract so
    # ScrollReveal still wires. The transpiler only wraps
    # sections that actually contain reveal resets, so this is safe even if some
    # framer usage is hover/mount-only.
    scroll_driven_plan = {
        "required": True,
        "library": "framer-motion",
        "hooks": ["useScroll", "useTransform", "scrollYProgress"],
        "evidence": "framer-motion detected (scrollDriven block absent — inferred)",
        "note": (
            "Map section scroll progress onto opacity/transform via "
            "scrollYProgress + useTransform; drive progress from the Lenis "
            "scroll source, never a raw window 'scroll' listener."
        ),
    }
else:
    scroll_driven_plan = {"required": False, "library": None, "hooks": [], "evidence": "", "note": ""}


# ── Scroll-scrub params (deterministic, from bundle-extraction.json) ──
# scrollDriven above is the high-level contract (library + hooks). bundle-
# extraction.json carries the CONCRETE per-site tables that _bundle_extraction.py
# pulls from the minified bundle: each Framer useScroll site's offset window plus
# its useTransform(progress, [input], [output]) mappings. Surface these so the
# generator emits real scroll-scrub motion (e.g. a background scale band crossing
# 1.0) instead of a generic opacity fade. Only sites that actually carry transform
# tables become scrub contracts; detection-only sites stay covered by scrollDriven.
_be_extractions = bundle_extraction.get("extractions") if isinstance(bundle_extraction, dict) else None
_fm_records = _be_extractions.get("framerMotion", []) if isinstance(_be_extractions, dict) else []
_scrub_sites = []
for _r in _fm_records if isinstance(_fm_records, list) else []:
    if not isinstance(_r, dict) or _r.get("kind") != "useScroll":
        continue
    _transforms = [t for t in (_r.get("transforms") or []) if isinstance(t, dict)]
    if not _transforms:
        continue
    _scrub_sites.append({
        "offset": _r.get("offset"),
        "transforms": _transforms,
        "source": _r.get("source"),
    })
if _scrub_sites:
    scroll_scrub_plan = {
        "required": True,
        "library": "framer-motion",
        "count": len(_scrub_sites),
        "sites": _scrub_sites[:24],
        "note": (
            "Each site maps a section's scrollYProgress (useScroll target+offset) "
            "onto a property via useTransform(progress, input, output). Emit "
            "useScroll({ target, offset }) + useTransform per site; wrap output in "
            "useSpring only where the bundle did. Drive progress from the Lenis "
            "scroll source, never a raw window 'scroll' listener."
        ),
    }
else:
    scroll_scrub_plan = {"required": False, "library": None, "count": 0, "sites": [], "note": ""}


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
# Deterministic signature-effect candidates (per-character scroll scrub, etc.)
# extracted from bundles by _signature_effects.py. Populate signatureEffects
# from high/medium-confidence candidates so generation gets a concrete contract
# regardless of whether the LLM enrichment pass ran; enrichment then refines
# names/components/selectors on top (it must not drop these). With no
# candidates, stay null so enrichment's "needs-enrichment" semantics hold.
_sig_doc = load("signature-effects-candidates.json", {})
_sig_candidates = _sig_doc.get("candidates", []) if isinstance(_sig_doc, dict) else []
_sig_strong = [
    c for c in _sig_candidates
    if isinstance(c, dict) and c.get("confidence") in ("high", "medium")
]
if _sig_strong:
    signature_effects = [
        {
            "name": c.get("name"),
            "effectType": c.get("effectType"),
            "selector": c.get("selector"),
            "library": c.get("library"),
            "trigger": c.get("trigger"),
            "animation": c.get("animation"),
            "component": f"components/ui/{c.get('name', 'SignatureEffect')}.tsx",
            "source": "deterministic-bundle-detection",
        }
        for c in _sig_strong
    ]
else:
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
    "scrollDriven": scroll_driven_plan,
    "scrollScrub": scroll_scrub_plan,
    "introAnimation": intro_plan,
    "signatureEffects": signature_effects,
    "forensicPreservation": forensic_preservation,
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
