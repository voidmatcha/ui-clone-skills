# ruff: noqa: I001, UP017, UP038
# mypy: disable-error-code="arg-type, assignment, func-returns-value, no-untyped-def, union-attr, var-annotated"
#
# Python 3.9 compat for PEP 604 unions used below — defer
# annotation evaluation so `X | Y` is parsed as a string.
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import sys
from datetime import datetime, timezone
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
CSS_RUNTIME_UNLOCK_SCAN_BYTES = 256 * 1024


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


HIDDEN_ROOT_SELECTOR_RE = re.compile(
    r"(^|[,\s])(?P<selector>(?:html|body)(?:[.#:[\]\w=\"'-]+)?|#(?:root|__next|app))\s*(?:,|\{)",
    re.IGNORECASE,
)
HIDDEN_DECL_RE = re.compile(
    r"(?:opacity\s*:\s*(?:0(?:\.0+)?)(?:\s*!important)?\b|"
    r"visibility\s*:\s*hidden(?:\s*!important)?\b|"
    r"display\s*:\s*none(?:\s*!important)?\b)",
    re.IGNORECASE,
)


def css_runtime_unlock_hints() -> list[dict[str, str]]:
    """Detect root/body hidden CSS that requires a runtime ready/unlock state.

    This is intentionally heuristic: it does not parse full CSS nesting, but it
    catches the production-bundle pattern that matters for generation planning
    (`body{opacity:0}`, `html.is-loading{visibility:hidden}`, `#root{display:none}`).
    The runtime gate remains the source of truth after implementation.

    Production CSS can be multi-megabyte and minified. A previous unbounded
    regex scan could spend minutes backtracking over long no-rule tails, so this
    scanner only inspects the prefix where first-paint root locks normally live
    and walks braces directly instead of applying a whole-file rule regex.
    """
    css_dir = ref_dir / "css"
    if not css_dir.is_dir():
        return []
    hints: list[dict[str, str]] = []
    seen_windows: set[str] = set()
    for css_file in sorted(css_dir.glob("*.css")):
        try:
            with css_file.open("rb") as handle:
                data = handle.read(CSS_RUNTIME_UNLOCK_SCAN_BYTES)
        except OSError:
            continue
        fingerprint = hashlib.sha256(data).hexdigest()
        if fingerprint in seen_windows:
            continue
        seen_windows.add(fingerprint)
        text = data.decode("utf-8", errors="ignore")
        search_from = 0
        while True:
            open_idx = text.find("{", search_from)
            if open_idx == -1:
                break
            close_idx = text.find("}", open_idx + 1)
            if close_idx == -1:
                break
            selector_start = max(text.rfind("}", 0, open_idx), text.rfind("{", 0, open_idx)) + 1
            selectors = text[selector_start:open_idx].strip()
            body = text[open_idx + 1 : close_idx]
            search_from = open_idx + 1
            if not HIDDEN_ROOT_SELECTOR_RE.search(selectors + "{"):
                continue
            decl = HIDDEN_DECL_RE.search(body)
            if not decl:
                continue
            hints.append({
                "file": str(css_file.relative_to(ref_dir)),
                "selector": selectors[:160],
                "declaration": decl.group(0)[:120],
            })
            if len(hints) >= 20:
                return hints
    return hints


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
runtime_unlock_hints = css_runtime_unlock_hints()
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
    "copy ref css/*.css with scripts/extract/sanitize-ref-css.sh into impl/src/ref-css and import them before overrides",
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
if runtime_unlock_hints:
    forensic_rules.append(
        "ref CSS hides html/body/root at first paint; reproduce the original ready/unlock state locally "
        "(for example remove loading classes or set body/root opacity visible after the intro/loader completes) "
        "before visual verification",
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
    "copyCssCommand": "bash scripts/extract/sanitize-ref-css.sh <ref-dir> <impl-root>" if forensic_required else None,
    "requiresRuntimeUnlock": bool(runtime_unlock_hints),
    "runtimeUnlockHints": runtime_unlock_hints,
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


# Install decisions read the `detected` presence map ONLY — not the whole
# external-sdks blob — so neither `derivedFrom` bundle filenames (e.g.
# "framer-motion-chunk.js") nor the additive `usedMotion` evidence field leak a
# spurious library into the install set. `detected` is the precise inventory.
external_detected = external_sdks.get("detected", external_sdks) if isinstance(external_sdks, dict) else external_sdks
detect_in(external_detected, "lenis", "lenis", "Smooth-scroll library detected in external-sdks.json")
detect_in(external_detected, "gsap", "gsap", "GSAP detected — install gsap + gsap/ScrollTrigger as needed")
detect_in(external_detected, "framer", "framer-motion", "Framer Motion detected")
detect_in(external_detected, "anime", "animejs", "Anime.js detected")
detect_in(external_detected, "auto-animate", "@formkit/auto-animate", "Auto-Animate detected")
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
# Path 1 — parser-schema bundles (_bundle_extraction.py) expose extractions.framerMotion[].
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

# Path 2 — hand-curated ui_clone.bundle_extraction schema exposes the scroll-scrub
# tables under constructionSites[] (no "extractions" key). Each trigger=="scroll-scrub"
# site carries a scrollOffset window + mappings[] (property/inputRange/outputRange) copied
# verbatim from the minified bundle. Reshape into the SAME {offset(JSON-string),
# transforms:[{property,input,output}]} contract emit-scroll-helpers.sh already parses, so
# the seam carries through to scrollScrubSites.ts/ScrollScrub.tsx and the scaffold auto-wrap.
# A mapping survives only when its property label LEADS with a recognized framer transform
# name (descriptive suffixes like " (title/card 1)"/" (px)" are annotations) and both ranges
# are all-numeric and equal length — the emitter drops anything else (symbolic keyframes
# 'P'/'n'/'l', unsupported props like width(vw)), so pre-filtering keeps the plan honest.
_SCRUB_PROP_MAP = {
    "scale": "scale", "scalex": "scaleX", "scaley": "scaleY",
    "opacity": "opacity", "x": "x", "y": "y", "rotate": "rotate",
    "translatex": "x", "translatey": "y",
    "width": "width", "height": "height", "borderradius": "borderRadius",
}
def _norm_scrub_prop(_p):
    if not isinstance(_p, str) or not _p.strip():
        return None
    _base = _p.strip().split()[0].lower()
    _base = _base.replace("-", "").replace("_", "")
    return _SCRUB_PROP_MAP.get(_base)
def _progress_domain(_v):
    # A scroll-scrub input range is a scrollYProgress fraction in [0,1]
    # (js-animation-extraction.md). A decompile that captured raw scrollY
    # pixels instead carries the same shape in a different domain, and
    # replaying it against progress silently freezes the element rather than
    # failing loudly. Out-of-domain input is dropped, never rescaled: the
    # conversion would need the capture session's document height, which is
    # exactly the coupling these bands must not acquire.
    if not isinstance(_v, list) or not _v:
        return False
    return max(_v) <= 1.0


def _numeric_scrub_range(_v):
    if not isinstance(_v, list) or len(_v) < 2:
        return None
    if not all(
        isinstance(_t, (int, float)) and not isinstance(_t, bool) and math.isfinite(_t)
        for _t in _v
    ):
        return None
    return _v


def _ascending_numeric_range(_v):
    _nums = _numeric_scrub_range(_v)
    if _nums is None:
        return None
    _ascending = all(_nums[_i] <= _nums[_i + 1] for _i in range(len(_nums) - 1))
    return _nums if _ascending else None


def _scroll_state_machine_transform(_input, _output, _unit):
    _inp = _ascending_numeric_range(_input)
    _out = _numeric_scrub_range(_output)
    if _inp is None or _out is None or len(_inp) != len(_out) or _unit != "px":
        return None
    return {
        "property": "top",
        "input": json.dumps(_inp),
        "output": json.dumps(_out),
        "unit": "px",
    }


_SCROLL_Y_TOP_RE = re.compile(
    r"^\s*transform\(\s*scrollY\s*,\s*"
    r"\[(?P<input>[^\]]+)\]\s*,\s*"
    r"\[(?P<output>(?:\s*['\"]-?\d+(?:\.\d+)?px['\"]\s*,?)+)\]\s*\)"
    r"(?:\s+clamped(?:\s+(?:—|-)\s+measured\s+"
    r"-?\d+(?:\.\d+)?px@-?\d+(?:\.\d+)?"
    r"(?:,\s*-?\d+(?:\.\d+)?px@-?\d+(?:\.\d+)?)*"
    r"\s+and\s+flat\s+-?\d+(?:\.\d+)?px\s+for\s+every\s+stop\s+>=\s+"
    r"-?\d+(?:\.\d+)?)?)?\s*$"
)


def _parse_number_list(_raw):
    _parts = [p.strip() for p in _raw.split(",")]
    if not _parts or any(not p for p in _parts):
        return None
    try:
        return [float(p) for p in _parts]
    except ValueError:
        return None


def _parse_px_list(_raw):
    _vals = re.findall(r"['\"]\s*(-?\d+(?:\.\d+)?)px\s*['\"]", _raw)
    if not _vals:
        return None
    return [float(v) for v in _vals]


def _scroll_state_machine_from_legacy_top(_value):
    if not isinstance(_value, str):
        return None
    _match = _SCROLL_Y_TOP_RE.fullmatch(_value)
    if not _match:
        return None
    return _scroll_state_machine_transform(
        _parse_number_list(_match.group("input")),
        _parse_px_list(_match.group("output")),
        "px",
    )


def _add_scroll_state_machine_site(_sites, _claimed, _site):
    if not _site:
        return
    _transforms = [
        _xf
        for _xf in (_site.get("transforms") or [])
        if isinstance(_xf, dict) and _xf.get("property") == "top"
    ]
    if not _transforms:
        return
    _key = (_site.get("selector"), "top")
    if not _key[0] or _key in _claimed:
        return
    _site["transforms"] = [_transforms[0]]
    _claimed.add(_key)
    _sites.append(_site)


_scroll_state_machine_sites = []
_scroll_state_machine_claims = set()
for _t in ((transition_spec.get("transitions") if isinstance(transition_spec, dict) else []) or []):
    if not isinstance(_t, dict):
        continue
    _anim = _t.get("animation")
    if not isinstance(_anim, dict):
        continue
    if _t.get("type") != "scroll-state-machine" and _anim.get("type") != "scroll-state-machine":
        continue
    _selector = _t.get("selector") or _t.get("target")
    if not isinstance(_selector, str) or not _selector:
        continue
    _spec_id = _t.get("id") if isinstance(_t.get("id"), str) else ""
    _channels = _anim.get("channels")
    if isinstance(_channels, list):
        for _ch in _channels:
            if not isinstance(_ch, dict):
                continue
            if _ch.get("property") != "top" or _ch.get("inputDomain") != "scroll-y-px":
                continue
            _xf = _scroll_state_machine_transform(
                _ch.get("inputRange"),
                _ch.get("outputRange"),
                _ch.get("unit"),
            )
            _add_scroll_state_machine_site(
                _scroll_state_machine_sites,
                _scroll_state_machine_claims,
                {
                    "specId": _spec_id,
                    "selector": _selector,
                    "inputDomain": "scroll-y-px",
                    "transforms": [_xf] if _xf else [],
                    "source": f"transition-spec.animation.channels:{_spec_id}",
                },
            )
    _xf = _scroll_state_machine_from_legacy_top(_anim.get("top"))
    _add_scroll_state_machine_site(
        _scroll_state_machine_sites,
        _scroll_state_machine_claims,
        {
            "specId": _spec_id,
            "selector": _selector,
            "inputDomain": "scroll-y-px",
            "transforms": [_xf] if _xf else [],
            "source": "transition-spec.animation.top",
        },
    )

scroll_state_machine_plan = {
    "required": bool(_scroll_state_machine_sites),
    "count": len(_scroll_state_machine_sites),
    "sites": _scroll_state_machine_sites,
    "note": (
        "Raw bundle-literal scrollY pixels are document-height independent and "
        "selector-scoped. Drive these state machines from window scrollY pixels "
        "and apply only the declared selector/property channel."
    ) if _scroll_state_machine_sites else "",
}

for _cs in (bundle_extraction.get("constructionSites", []) if isinstance(bundle_extraction, dict) else []):
    if not isinstance(_cs, dict) or _cs.get("trigger") != "scroll-scrub":
        continue
    _xf = []
    _seen_props = set()
    for _m in (_cs.get("mappings") or []):
        if not isinstance(_m, dict):
            continue
        _prop = _norm_scrub_prop(_m.get("property"))
        if not _prop or _prop in _seen_props:
            continue
        _inp = _numeric_scrub_range(_m.get("inputRange"))
        _out = _numeric_scrub_range(_m.get("outputRange"))
        if _inp is None or _out is None or len(_inp) != len(_out):
            continue
        if not _progress_domain(_inp):
            continue
        _seen_props.add(_prop)
        _xf.append({
            "property": _prop,
            "input": json.dumps(_inp),
            "output": json.dumps(_out),
        })
    if not _xf:
        continue
    _entry = {"transforms": _xf, "source": _cs.get("id")}
    _off = _cs.get("scrollOffset")
    if isinstance(_off, list) and len(_off) == 2:
        _entry["offset"] = json.dumps(_off)
    _scrub_sites.append(_entry)

# Path 3 — runtime-measured scroll-scrub curve. extraction_artifacts.py mines
# animation-runtime-dump.json scrollLinkedStyles into transition-spec.json
# transitions[].animation.scrollKeyframes {input, outputs{prop:series}} — the
# only source for framer sites whose useTransform bands are computed at runtime
# (no bundle string range to parse). Reshape into the SAME numeric-band contract
# Path 1/2 emit so emit-scroll-helpers.sh binds the measured curve (often a
# back-loaded ease-in) instead of a flat lerp. outputs values are raw inline-
# style strings: opacity is a bare number; a transform string decomposes into
# one band per single-function channel (scale/x/y/rotate). Scalar CSS lengths
# such as width/borderRadius are retained; matrix/calc values drop honestly.
def _coerce_scalar(_s):
    try:
        if isinstance(_s, bool):
            return None
        if isinstance(_s, (int, float)):
            return float(_s)
        _v = str(_s).strip()
        if not _v:
            return None
        if _v.lower() in ("auto", "none", "unset", "initial", "inherit"):
            return None
        _m = re.fullmatch(r"([-+]?\d*\.?\d+)(px|%)?", _v)
        if _m:
            return float(_m.group(1))
        return float(str(_s).strip())
    except (TypeError, ValueError):
        return None


def _scalar_unit(_s):
    if isinstance(_s, bool):
        return None
    if isinstance(_s, (int, float)):
        return ""
    _m = re.fullmatch(r"[-+]?\d*\.?\d+(px|%)?", str(_s).strip())
    return (_m.group(1) or "") if _m else None


_LENGTH_SCRUB_PROPS = {"width", "height", "borderRadius"}
_TRANSFORM_FUNC_MAP = {
    "scale": "scale", "scalex": "scaleX", "scaley": "scaleY",
    "translatex": "x", "translatey": "y", "rotate": "rotate",
}
def _decompose_transform_series(_series):
    _parsed = []
    _channels = set()
    for _frame in _series:
        _text = str(_frame or "").strip().lower()
        if _text in ("", "none"):
            _parsed.append({})
            continue
        _values = {}
        for _name, _val in re.findall(
            r"([a-zA-Z]+)\(([-0-9.]+)[a-z%]*\)", str(_frame)
        ):
            _ch = _TRANSFORM_FUNC_MAP.get(_name.lower())
            _num = _coerce_scalar(_val)
            if _ch is not None and _num is not None:
                _values[_ch] = _num
                _channels.add(_ch)
        # An unsupported non-identity transform cannot be completed honestly.
        if not _values:
            return {}
        _parsed.append(_values)
    _identity = {
        "scale": 1.0,
        "scaleX": 1.0,
        "scaleY": 1.0,
        "x": 0.0,
        "y": 0.0,
        "rotate": 0.0,
    }
    return {
        _ch: [_frame.get(_ch, _identity[_ch]) for _frame in _parsed]
        for _ch in sorted(_channels)
    }


_SCROLL_ANCHORS = {"start": 0.0, "center": 0.5, "end": 1.0}


def _resolved_scroll_offset(_animation):
    if not isinstance(_animation, dict):
        return None
    _raw = _animation.get("offset")
    if isinstance(_raw, list) and len(_raw) == 2 and all(
        isinstance(_value, str) for _value in _raw
    ):
        return _raw
    if not isinstance(_raw, str):
        return None
    try:
        _parsed = json.loads(_raw)
    except (json.JSONDecodeError, TypeError):
        _parsed = None
    if isinstance(_parsed, list) and len(_parsed) == 2 and all(
        isinstance(_value, str) for _value in _parsed
    ):
        return _parsed
    # Framer bundle evidence commonly preserves a reduced-motion conditional
    # instead of the resolved default branch. Normal motion chooses start/start.
    if (
        "start " in _raw
        and '?"end":"start"' in _raw.replace(" ", "")
        and "end end" in _raw
    ):
        return ["start start", "end end"]
    return None


def _section_for_selector(_selector):
    if not isinstance(_selector, str) or not _selector.strip():
        return None
    _selector = _selector.strip()
    for _section in sections if isinstance(sections, list) else []:
        if not isinstance(_section, dict):
            continue
        if _selector.startswith("#"):
            if str(_section.get("id") or "") == _selector[1:]:
                return _section
            continue
        if _selector.startswith("."):
            _required = [
                _part for _part in _selector.split(".") if _part
            ]
            _classes = set(
                str(_section.get("className") or _section.get("class") or "").split()
            )
            if _required and set(_required).issubset(_classes):
                return _section
            continue
        if str(_section.get("tag") or "").lower() == _selector.lower():
            return _section
    return None


def _captured_viewport_height(_dump):
    if isinstance(_dump, dict):
        _viewport = _dump.get("viewport")
        if isinstance(_viewport, dict):
            _height = _coerce_scalar(_viewport.get("height"))
            if _height is not None and _height > 0:
                return _height
    _structure = load("structure.json", {})
    if isinstance(_structure, dict):
        _styles = _structure.get("styles")
        if isinstance(_styles, dict):
            _height = _coerce_scalar(_styles.get("min-height"))
            if _height is not None and 0 < _height <= 4096:
                return _height
    return None


def _captured_max_scroll(_dump, _viewport_height):
    if isinstance(_dump, dict):
        _document = _dump.get("documentScroll") or _dump.get("document")
        if isinstance(_document, dict):
            _max_scroll = _coerce_scalar(_document.get("maxScroll"))
            if _max_scroll is not None and _max_scroll > 0:
                return _max_scroll
            _doc_height = _coerce_scalar(_document.get("scrollHeight"))
            if _doc_height is not None and _doc_height > _viewport_height:
                return _doc_height - _viewport_height
    _doc_height = None
    if isinstance(section_map, dict):
        _doc_height = _coerce_scalar(section_map.get("docHeight"))
    if _doc_height is not None and _doc_height > _viewport_height:
        return _doc_height - _viewport_height
    return None


def _offset_scroll_y(_offset, _top, _height, _viewport_height):
    if not isinstance(_offset, str):
        return None
    _parts = _offset.lower().split()
    if len(_parts) != 2:
        return None
    _target_anchor = _SCROLL_ANCHORS.get(_parts[0])
    _viewport_anchor = _SCROLL_ANCHORS.get(_parts[1])
    if _target_anchor is None or _viewport_anchor is None:
        return None
    return (
        _top
        + _height * _target_anchor
        - _viewport_height * _viewport_anchor
    )


def _target_offset_contract(_scope, _offset, _dump):
    _section = _section_for_selector(_scope)
    if not _section or not _offset:
        return None
    _top = _coerce_scalar(_section.get("top"))
    _height = _coerce_scalar(_section.get("height"))
    _viewport_height = _captured_viewport_height(_dump)
    if None in (_top, _height, _viewport_height) or _height <= 0:
        return None
    _max_scroll = _captured_max_scroll(_dump, _viewport_height)
    if _max_scroll is None:
        return None
    _start = _offset_scroll_y(_offset[0], _top, _height, _viewport_height)
    _end = _offset_scroll_y(_offset[1], _top, _height, _viewport_height)
    if _start is None or _end is None or abs(_end - _start) < 1:
        return None
    return {
        "offset": _offset,
        "start": _start,
        "end": _end,
        "maxScroll": _max_scroll,
    }


def _remap_document_band(_inputs, _values, _contract):
    if not _contract:
        return _inputs, _values
    _mapped = []
    _span = _contract["end"] - _contract["start"]
    for _input, _value in zip(_inputs, _values):
        _scroll_y = _input * _contract["maxScroll"]
        _progress = max(0.0, min(1.0, (_scroll_y - _contract["start"]) / _span))
        _progress = round(_progress, 6)
        if _mapped and _mapped[-1][0] == _progress:
            _mapped[-1] = (_progress, _value)
        else:
            _mapped.append((_progress, _value))
    if len(_mapped) < 2:
        return _inputs, _values
    return [item[0] for item in _mapped], [item[1] for item in _mapped]


def _runtime_scroll_scrub_sites(_dump):
    if not isinstance(_dump, dict):
        return []
    rows = _dump.get("scrollLinkedStyles")
    if not isinstance(rows, list):
        return []
    out = []
    # Runtime sampling records document-scroll fractions, while the individual
    # selectors are often deliberately short (``svg``, ``g#even``). Scope them
    # to the sole declared scrub target when the spec provides one so replay
    # cannot animate unrelated page SVGs. Multiple scrub roots are ambiguous;
    # leave those unscoped rather than inventing a pairing.
    _scope_candidates = []
    _scope_offsets = {}
    for _transition in (
        (transition_spec.get("transitions") or [])
        if isinstance(transition_spec, dict)
        else []
    ):
        if not isinstance(_transition, dict):
            continue
        _animation = _transition.get("animation")
        _kind = (
            str(_animation.get("type") or "").lower()
            if isinstance(_animation, dict)
            else str(_animation or "").lower()
        )
        _trigger = str(_transition.get("trigger") or "").lower()
        _target = _transition.get("target") or _transition.get("selector")
        if "scroll" in _trigger and "scrub" in _kind and isinstance(_target, str):
            if _target.strip() and _target.strip() not in _scope_candidates:
                _scope_candidates.append(_target.strip())
            _offset = _resolved_scroll_offset(_animation)
            if _offset:
                _scope_offsets[_target.strip()] = _offset
    _scope = _scope_candidates[0] if len(_scope_candidates) == 1 else None
    _target_contract = _target_offset_contract(
        _scope,
        _scope_offsets.get(_scope),
        _dump,
    )
    _selector_counts = {}
    for _row in rows:
        if not isinstance(_row, dict):
            continue
        _selector = _row.get("selector")
        _varies = _row.get("varies")
        _by_scroll = _row.get("byScroll")
        if (
            not isinstance(_selector, str)
            or not isinstance(_varies, list)
            or not isinstance(_by_scroll, dict)
        ):
            continue
        # A latched row is a discrete state the return sweep disagreed with;
        # interpolating it across a band renders it permanently half-applied.
        if _row.get("latched") is True:
            continue
        _frames = {k: v for k, v in _by_scroll.items() if isinstance(k, str) and isinstance(v, dict)}
        _positions = []
        for _k, _v in _frames.items():
            if not isinstance(_k, str) or not isinstance(_v, dict):
                continue
            try:
                _positions.append((float(_k), _k))
            except (TypeError, ValueError):
                continue
        if len(_positions) < 2:
            continue
        _positions.sort(key=lambda it: it[0])
        _fracs = [_k for _, _k in _positions]
        if len(_fracs) < 2:
            continue
        _input = [_coerce_scalar(_f) for _f in _fracs]
        if any(_x is None for _x in _input):
            continue
        _xf = []
        _seen = set()
        for _prop in _varies:
            if not isinstance(_prop, str):
                continue
            _series = [_coerce_scalar(_frames[_f].get(_prop)) for _f in _fracs]
            _unit = None
            if _prop.strip().lower() == "transform":
                _channels = _decompose_transform_series(
                    [_frames[_f].get(_prop) for _f in _fracs]
                )
            else:
                _norm = _norm_scrub_prop(_prop)
                if _norm in _LENGTH_SCRUB_PROPS:
                    _units = {
                        _scalar_unit(_frames[_f].get(_prop)) for _f in _fracs
                    }
                    if None in _units or len(_units) != 1:
                        _norm = None
                    else:
                        _unit = next(iter(_units))
                _channels = (
                    {_norm: _series}
                    if _norm and all(_v is not None for _v in _series)
                    else {}
                )
            for _channel, _values in _channels.items():
                if not _channel or _channel in _seen:
                    continue
                _band_input, _band_values = _remap_document_band(
                    _input, _values, _target_contract
                )
                if len(_band_input) != len(_band_values):
                    continue
                _seen.add(_channel)
                _band = {
                    "property": _channel,
                    "input": json.dumps(_band_input),
                    "output": json.dumps(_band_values),
                }
                if _channel in _LENGTH_SCRUB_PROPS and _unit is not None:
                    _band["unit"] = _unit
                _xf.append(_band)
        if not _xf:
            continue
        _selector_index = _selector_counts.get(_selector, 0)
        _selector_counts[_selector] = _selector_index + 1
        _entry = {
            "transforms": _xf,
            "selector": _selector,
            "selectorIndex": _selector_index,
            "progressSource": (
                "target-offset" if _target_contract else "document-progress"
            ),
            "source": "animation-runtime-dump.json:scrollLinkedStyles",
        }
        if _scope:
            _entry["target"] = _scope
            _entry["scope"] = _scope
        if _target_contract:
            _entry["offset"] = json.dumps(_target_contract["offset"])
        out.append(_entry)
    return out

def _runtime_scroll_latch_sites(_dump):
    # Latched rows are discrete states. Key them by progress fraction, never by
    # capture-session pixels, so the threshold survives a document-height
    # change; the driver resolves the fraction against the live scroll range.
    if not isinstance(_dump, dict):
        return []
    _rows = _dump.get("scrollLinkedStyles")
    if not isinstance(_rows, list):
        return []
    _out = []
    _counts = {}
    for _row in _rows:
        if not isinstance(_row, dict) or _row.get("latched") is not True:
            continue
        _selector = _row.get("selector")
        _varies = _row.get("varies")
        _by_scroll = _row.get("byScroll")
        if (
            not isinstance(_selector, str)
            or not isinstance(_varies, list)
            or not isinstance(_by_scroll, dict)
        ):
            continue
        _points = []
        for _key, _vals in _by_scroll.items():
            if not isinstance(_vals, dict):
                continue
            try:
                _points.append((float(_key), _vals))
            except (TypeError, ValueError):
                continue
        if len(_points) < 2:
            continue
        _points.sort(key=lambda _pair: _pair[0])
        _end_state = {
            _prop: _points[-1][1].get(_prop)
            for _prop in _varies
            if _points[-1][1].get(_prop) is not None
        }
        if not _end_state:
            continue
        _progress = _points[-1][0]
        for _offset, _vals in _points:
            if all(_vals.get(_prop) == _value for _prop, _value in _end_state.items()):
                _progress = _offset
                break
        _index = _counts.get(_selector, 0)
        _counts[_selector] = _index + 1
        _out.append(
            {
                "selector": _selector,
                "selectorIndex": _index,
                "progress": _progress,
                "endState": _end_state,
            }
        )
    return _out


def _documented_output_ranges(_anim):
    # skills/ui-reverse-engineering/js-animation-extraction.md teaches
    # useTransform(progress, inputRange, outputRange); decompiled specs carry
    # one <channel>OutputRange per animated channel. Channels the scrub driver
    # cannot apply (layoutOutputRange -> "top") do not normalise and are
    # dropped rather than guessed at.
    if not isinstance(_anim, dict):
        return {}
    _out = {}
    for _key, _series in _anim.items():
        if not isinstance(_key, str) or not _key.endswith("OutputRange"):
            continue
        if not isinstance(_series, list):
            continue
        _channel = _norm_scrub_prop(_key[: -len("OutputRange")])
        if _channel:
            _out[_channel] = _series
    _bare = _anim.get("outputRange")
    if isinstance(_bare, list):
        _channel = _norm_scrub_prop(_anim.get("property"))
        if _channel and _channel not in _out:
            _out[_channel] = _bare
    return _out


for _t in ((transition_spec.get("transitions") if isinstance(transition_spec, dict) else []) or []):
    if not isinstance(_t, dict):
        continue
    _anim = _t.get("animation")
    if not isinstance(_anim, dict) or _anim.get("type") != "scroll-scrub":
        continue
    _sk = _anim.get("scrollKeyframes")
    if isinstance(_sk, dict):
        _shape = "scrollKeyframes"
        _in_raw = _sk.get("input")
        _outputs = _sk.get("outputs")
    else:
        _shape = "inputRange"
        # A per-breakpoint decompile names the domain inputRangeDesktop /
        # inputRangeMobile; the clone is generated desktop-first.
        _in_raw = _anim.get("inputRange")
        if not isinstance(_in_raw, list):
            _in_raw = _anim.get("inputRangeDesktop")
        if not isinstance(_in_raw, list):
            _in_raw = _anim.get("inputRangeMobile")
        _outputs = _documented_output_ranges(_anim)
    if not isinstance(_in_raw, list) or len(_in_raw) < 2:
        continue
    _input = [_coerce_scalar(_x) for _x in _in_raw]
    if any(_v is None for _v in _input):
        continue
    if not _progress_domain(_input):
        continue
    if not isinstance(_outputs, dict) or not _outputs:
        continue
    _xf = []
    _seen = set()
    for _prop, _series in _outputs.items():
        if not isinstance(_series, list) or len(_series) != len(_input):
            continue
        if _prop == "transform":
            _channels = _decompose_transform_series(_series)
        else:
            _norm = _norm_scrub_prop(_prop)
            _nums = [_coerce_scalar(_v) for _v in _series]
            _channels = {_norm: _nums} if (_norm and all(_n is not None for _n in _nums)) else {}
        for _ch, _nums in _channels.items():
            if not _ch or _ch in _seen or len(_nums) != len(_input):
                continue
            _seen.add(_ch)
            _xf.append({
                "property": _ch,
                "input": json.dumps(_input),
                "output": json.dumps(_nums),
            })
    if not _xf:
        continue
    _sk_entry = {"transforms": _xf, "source": "transition-spec." + _shape + ":" + str(_t.get("id") or "")}
    _sk_sel = _t.get("selector") or _t.get("target")
    if isinstance(_sk_sel, str) and _sk_sel:
        _sk_entry["selector"] = _sk_sel
    # The scrollScrub note instructs the generator to emit useScroll({target,
    # offset}) and to spring the output only where the bundle did. Both facts
    # live on the decompiled animation, so drop neither: without the offset the
    # site silently falls back to raw document progress, and without the spring
    # params a sprung band replays as a bare lerp.
    _sk_off = _resolved_scroll_offset(_anim)
    if _sk_off:
        _sk_entry["offset"] = json.dumps(_sk_off)
    _sk_spring = _anim.get("spring")
    if isinstance(_sk_spring, dict):
        _sk_spring = {
            _k: _v
            for _k, _v in _sk_spring.items()
            if isinstance(_v, (int, float)) and not isinstance(_v, bool)
        }
        if _sk_spring:
            _sk_entry["spring"] = _sk_spring
    _scrub_sites.append(_sk_entry)

# A modelled curve beats a sample of it: when a spec/bundle site already
# describes a selector, the runtime row for that selector is redundant and
# would double-drive the element. Runtime rows carry their own cap so a
# modelled site can never displace measured data off the end of the list.
_spec_claimed_selectors = {
    _s.get("selector") for _s in _scrub_sites if isinstance(_s.get("selector"), str)
}
_runtime_sites = [
    _s
    for _s in _runtime_scroll_scrub_sites(load("animation-runtime-dump.json", {}))
    if _s.get("selector") not in _spec_claimed_selectors
]
_scrub_sites.extend(_runtime_sites[:24])

if _scrub_sites:
    scroll_scrub_plan = {
        "required": True,
        "library": "framer-motion",
        "count": len(_scrub_sites),
        "sites": _scrub_sites,
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

_latch_sites = _runtime_scroll_latch_sites(load("animation-runtime-dump.json", {}))
scroll_latch_plan = {
    "required": bool(_latch_sites),
    "count": len(_latch_sites),
    "sites": _latch_sites,
    "note": (
        "Discrete scroll states the return sweep proved do not reverse. Apply "
        "each endState once scroll progress passes its fraction; never "
        "interpolate them, that is what renders every state half-applied."
    ) if _latch_sites else "",
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
# Interactive-physics canvas (matter.js/verlet/planck drop-in bodies) must be
# REBUILT with the engine, not recorded as a video loop. Surface the engine +
# constants so generation reproduces the running simulation. Positively gated on
# hasPhysics, so a decorative shader/Spline canvas adds nothing here.
if isinstance(canvas_webgl, dict) and canvas_webgl.get("hasPhysics"):
    _eng = canvas_webgl.get("physicsEngine") if isinstance(canvas_webgl.get("physicsEngine"), dict) else {}
    _live = _eng.get("liveEngine") if isinstance(_eng.get("liveEngine"), dict) else None
    canvas_plan["renderKind"] = canvas_webgl.get("renderKind", "interactive-physics")
    canvas_plan["physics"] = {
        "required": True,
        "engine": _eng.get("name"),
        "version": _eng.get("version"),
        "constants": {"gravity": _live.get("gravity"), "bodyCount": _live.get("bodyCount")} if isinstance(_live, dict) else None,
        "constantsSource": "runtime-engine" if isinstance(_live, dict) and _live.get("gravity") else "library-default+bundle-grep",
        "note": "Reproduce interactive physics with the named engine (spawn/drop/append); "
                "a static canvas-replay video does NOT satisfy this. Non-deterministic → "
                "runtime verdict is 'unmeasurable', but the impl canvas must actually run.",
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
provenance_sources = (
    "section-map.json",
    "styles.json",
    "css/variables.txt",
    "signature-effects-candidates.json",
    "structure.json",
    "animations-detected.json",
    "transition-spec.json",
    "element-roles.json",
    "element-groups.json",
    "layout-decisions.json",
    "component-map.json",
    "asset-substitution.json",
    "font-parity.json",
    "bundle-extraction.json",
    "sticky-elements.json",
    "hidden-elements.json",
    "mobile-swap.json",
    "animation-init-styles.json",
    "external-sdks.json",
    "bundle-map.json",
    "scroll-engine.json",
    "paid-features.json",
    "canvas-webgl-detection.json",
    "required-media.json",
    "dom-scaffold.json",
    "extracted.json",
    "head.json",
    "css/*.css",
)
source_hashes = {}
for relative_path in provenance_sources:
    if relative_path == "css/*.css":
        css_entries = [
            (
                path.relative_to(ref_dir).as_posix(),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in sorted((ref_dir / "css").glob("*.css"))
            if path.is_file()
        ]
        source_hashes[relative_path] = (
            hashlib.sha256(
                json.dumps(
                    css_entries,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            ).hexdigest()
            if css_entries
            else None
        )
        continue
    source_path = ref_dir / relative_path
    source_hashes[relative_path] = (
        hashlib.sha256(source_path.read_bytes()).hexdigest()
        if source_path.is_file()
        else None
    )

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
    "scrollStateMachine": scroll_state_machine_plan,
    "scrollScrub": scroll_scrub_plan,
    "scrollLatch": scroll_latch_plan,
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
    "provenance": {
        "source": "scripts/extract/generation-plan.sh",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "hashAlgorithm": "sha256",
        "sourceHashes": source_hashes,
    },
}

out_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
print(f"✓ generation-plan.json written → {out_path}")
print(f"  components: {len(components)} | libraries: {sorted(detected_libs.keys())}")
print(f"  sticky: {len(sticky_plan)} | hidden: {len(hidden_plan)} | mobile-swap: {len(mobile_swap_plan)}")
print(f"  smoothScroll: {smooth_scroll_plan['required']} | introAnimation: {intro_plan['required']}")
print(f"  arch layers: tokens={arch_layers['tokens']} ds-components={arch_layers['dsComponents']} constants={arch_layers['constants']}")
