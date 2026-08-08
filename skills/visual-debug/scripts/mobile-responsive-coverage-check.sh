#!/usr/bin/env bash
# mobile-responsive-coverage-check.sh — static check that the impl carries
# IMPL-AUTHORED responsiveness proportional to a responsive reference. The
# browser-probe mobile-viewport-parity gate only verifies "no horizontal
# overflow + renders", so a generic rebuild that ships the desktop layout with
# almost no media queries / fluid sizing passes it while looking broken on
# mobile.
#
# Mirror-blindness fix (parity with transition-spec-coverage): the verbatim CSS
# mirror (src/styles/from-ref/, src/ref-css/, + UI_CLONE_GENERATED_EVIDENCE_DIRS)
# reproduces every ref @media rule by construction, so counting it as impl
# responsiveness let a px-baked clone that merely COPIED the ref CSS pass. This
# check now counts only impl-authored responsiveness: the impl's OWN CSS media
# queries / fluid units, matchMedia / resize / ResizeObserver listeners, and
# responsive utilities in emitted JSX — the mirror is excluded. It also reports
# inline box-model px baked into JSX on class tokens the ref declares
# responsively (the "inline px wins the cascade at every viewport" defect).
#
# Usage: mobile-responsive-coverage-check.sh <ref-dir> [<impl-src-dir>]
# Output: <ref-dir>/mobile-responsive-coverage.json
# Exit: 0 pass/skip, 1 under-responsive, 2 setup error.
# Severity: verification-plan.sh registers this lexical metric as WARN. Its
# artifact remains pass/fail diagnostic evidence, while runtime responsive
# checks decide blocking status.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_SRC_DIR="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: mobile-responsive-coverage-check.sh <ref-dir> [<impl-src-dir>]" >&2
  exit 2
fi

if [ -z "$IMPL_SRC_DIR" ]; then
  PLUGIN_ROOT_CAND="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
  for cand_root in "$PLUGIN_ROOT_CAND" "$(cd "$(dirname "$0")/../../.." && pwd)"; do
    [ -z "$cand_root" ] && continue
    RESOLVER="$cand_root/scripts/extract/find-impl-root.sh"
    if [ -f "$RESOLVER" ]; then
      IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
      if [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT/src" ]; then
        IMPL_SRC_DIR="$IMPL_ROOT/src"
        break
      fi
    fi
  done
fi

OUT_PATH="$REF_DIR/mobile-responsive-coverage.json"

python3 - "$REF_DIR" "$IMPL_SRC_DIR" "$OUT_PATH" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_src = sys.argv[2]
out_path = Path(sys.argv[3])

_MEDIA = re.compile(r"@media")
_CLAMP = re.compile(r"clamp\(")
_VW = re.compile(r"\d+(?:\.\d+)?v[wh]\b")
_UTIL = re.compile(r"\b(?:sm|md|lg|xl|2xl):")
# JS-driven responsiveness: a matchMedia read, a resize listener, a ResizeObserver,
# or a React media-query hook. Presence means the impl adapts layout to width at
# runtime rather than freezing a desktop snapshot.
_JS_RESP = re.compile(
    r"matchMedia|ResizeObserver|useMediaQuery|useWindowSize|useBreakpoint"
    r"|addEventListener\(\s*['\"]resize['\"]|onresize\b|window\.onresize"
)
# Inline box-model px baked into emitted JSX (style={{ width: '320px' }} /
# style=\"width:320px\"). A property fixed to px in the JSX wins the cascade at
# every viewport regardless of the mirrored @media rules.
_INLINE_PX = re.compile(
    r"\b(?:width|height|min-width|min-height|max-width|max-height|margin"
    r"|padding|top|left|right|bottom|font-size|line-height|gap|flex-basis)"
    r"\s*:\s*['\"]?\d+px",
    re.IGNORECASE,
)
# Mirror dirs whose CSS is a verbatim copy of the ref — never counts as impl work.
_MIRROR_DIRS = {"from-ref", "ref-css"} | {
    d.strip()
    for part in os.environ.get("UI_CLONE_GENERATED_EVIDENCE_DIRS", "").replace(":", ",").split(",")
    for d in [part]
    if d.strip()
}
_VENDOR_DIRS = {"node_modules", "dist", "build", ".next", ".git", "coverage"}
_SKIP_DIRS = _MIRROR_DIRS | _VENDOR_DIRS


def write(obj: dict, code: int) -> None:
    out_path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    print(f"mobile-responsive-coverage: {obj['status']}")
    sys.exit(code)


def signals(text: str, *, utilities: bool) -> int:
    n = len(_MEDIA.findall(text)) + len(_CLAMP.findall(text)) + len(_VW.findall(text))
    if utilities:
        n += len(_UTIL.findall(text))
    return n


# Reference responsive density: ref CSS chunks + styles.json.
ref_text_parts = []
css_dir = ref_dir / "css"
if css_dir.is_dir():
    for p in css_dir.glob("*.css"):
        try:
            ref_text_parts.append(p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
sj = ref_dir / "styles.json"
if sj.is_file():
    try:
        ref_text_parts.append(sj.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        pass
ref_text = "\n".join(ref_text_parts)
ref_signals = signals(ref_text, utilities=False)

# Class base-tokens the ref declares INSIDE @media blocks — the classes whose
# layout the ref changes across breakpoints. Baking inline px on these in the
# impl freezes them. (Approximate scan: class tokens in the window after each
# @media, CSS-modules hash stripped to base.)
ref_responsive_tokens: set[str] = set()
for m in re.finditer(r"@media", ref_text):
    window = ref_text[m.end():m.end() + 2000]
    for tok in re.findall(r"\.([A-Za-z_][\w-]*)", window):
        base = re.sub(r"__[A-Za-z0-9_-]+$", "", tok)
        if len(base) >= 3:
            ref_responsive_tokens.add(base)

# Is the ref actually responsive? Require either detected breakpoints or a
# meaningful responsive density — otherwise this check does not apply.
bp = ref_dir / "detected-breakpoints.json"
bp_count = 0
if bp.is_file():
    try:
        d = json.loads(bp.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("breakpoints"), list):
            bp_count = len(d["breakpoints"])
        elif isinstance(d, list):
            bp_count = len(d)
    except (OSError, json.JSONDecodeError):
        bp_count = 0
ref_responsive = bp_count >= 2 or ref_signals >= 8
if not ref_responsive:
    write({"schemaVersion": 1, "status": "skip",
           "reason": "ref is not responsive (no breakpoints / low density) — check N/A",
           "refSignals": ref_signals, "refBreakpoints": bp_count}, 0)

if not impl_src or not Path(impl_src).is_dir():
    write({"schemaVersion": 1, "status": "skip",
           "reason": "impl/src not found"}, 0)

# Walk impl/src, splitting IMPL-AUTHORED files from the verbatim CSS mirror.
# Only impl-authored files count toward responsiveness; the mirror is reported
# separately so the inflation it used to cause is visible, never credited.
CSS_EXT = {".css", ".scss", ".sass", ".less", ".styl", ".pcss"}
JS_EXT = {".tsx", ".jsx", ".ts", ".js", ".mjs", ".cjs", ".vue", ".svelte"}
impl_css_parts: list[str] = []
impl_js_parts: list[str] = []
mirror_parts: list[str] = []
for root, dirs, files in os.walk(impl_src):
    in_mirror = any(seg in _MIRROR_DIRS for seg in Path(root).parts)
    dirs[:] = [d for d in dirs if d not in _VENDOR_DIRS]
    for name in files:
        ext = Path(name).suffix.lower()
        if ext not in CSS_EXT and ext not in JS_EXT:
            continue
        try:
            text = Path(root, name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if in_mirror:
            mirror_parts.append(text)
        elif ext in CSS_EXT:
            impl_css_parts.append(text)
        else:
            impl_js_parts.append(text)

impl_css_text = "\n".join(impl_css_parts)
impl_js_text = "\n".join(impl_js_parts)
impl_css_media = len(_MEDIA.findall(impl_css_text))
impl_css_signals = signals(impl_css_text, utilities=False)
impl_js_signals = signals(impl_js_text, utilities=True) + len(_JS_RESP.findall(impl_js_text))
impl_authored = impl_css_signals + impl_js_signals
mirror_signals = signals("\n".join(mirror_parts), utilities=False)

# Inline box-model px baked into emitted JSX, and how many sit on a class the ref
# declares responsively (the "inline px wins the cascade" defect).
inline_px_total = len(_INLINE_PX.findall(impl_js_text))
inline_px_on_responsive = 0
for m in _INLINE_PX.finditer(impl_js_text):
    window = impl_js_text[max(0, m.start() - 240):m.start() + 40]
    cls_m = re.search(r"class(?:Name)?\s*=\s*[\"'{`]([^\"'}`]*)", window)
    if cls_m:
        toks = {re.sub(r"__[A-Za-z0-9_-]+$", "", t) for t in cls_m.group(1).split()}
        if toks & ref_responsive_tokens:
            inline_px_on_responsive += 1

# Generous proportional floor — impl needs only ~10% of the ref's responsive
# density (min 3). A forensic clone that keeps its own fluid CSS clears this; a
# generic rebuild that ships ~no impl-authored responsiveness does not.
floor = max(3, int(ref_signals * 0.1))
under_floor = impl_authored < floor
# A responsive ref where the impl authored ZERO of its own media queries and
# bakes inline px on responsive classes is frozen-desktop regardless of density.
frozen_desktop = bp_count >= 2 and impl_css_media == 0 and inline_px_on_responsive >= 1
status = "fail" if (under_floor or frozen_desktop) else "pass"

if under_floor:
    reason = (
        f"impl-authored responsive density {impl_authored} < floor {floor} "
        f"(ref density {ref_signals}); the mirror contributed {mirror_signals} "
        "signals that no longer count. The impl likely renders a fixed desktop "
        "layout at mobile widths — add its OWN media queries / fluid sizing / "
        "matchMedia listeners, do not lean on the copied ref CSS."
    )
elif frozen_desktop:
    reason = (
        f"impl authored 0 media queries and bakes inline px on "
        f"{inline_px_on_responsive} element(s) whose class the ref declares "
        "responsively — inline px wins the cascade at every viewport, so the "
        "ref's @media rules are dead weight. Emit className-only (let the "
        "mirrored @media drive layout) or author responsive impl CSS."
    )
else:
    reason = "impl carries impl-authored responsiveness proportional to ref"

write({
    "schemaVersion": 2,
    "status": status,
    "refSignals": ref_signals,
    "refBreakpoints": bp_count,
    "refResponsiveClassTokens": len(ref_responsive_tokens),
    "implAuthoredSignals": impl_authored,
    "implCssSignals": impl_css_signals,
    "implCssMediaQueries": impl_css_media,
    "implJsSignals": impl_js_signals,
    "mirrorSignalsExcluded": mirror_signals,
    "inlineBoxModelPx": inline_px_total,
    "inlinePxOnResponsiveClass": inline_px_on_responsive,
    "floor": floor,
    "reason": reason,
}, 0 if status == "pass" else 1)
PY
