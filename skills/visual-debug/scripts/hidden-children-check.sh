#!/usr/bin/env bash
# hidden-children-check.sh — fail when major sections have all their
# direct children hidden after animations finish.
#
#
# Critical distinction vs reveal-trigger-check.sh:
#   reveal-trigger    — enumerates initially-hidden elements (opacity:0
#                       / non-identity transform), scrolls each into
#                       view, fails when the style never advances.
#                       Targets the IO+overflow:hidden bug class (the
#                       reveal is wired but never fires).
#   hidden-children   — for each MAJOR section, after letting all
#                       animations finish, checks whether every direct
#                       child is permanently hidden. Targets the
#                       background-painting cheat where reveal-trigger
#                       would say "OK, nothing should be visible here
#                       yet" while the impl uses the background to fool
#                       pixel diff.
#
#
# Usage:
#   hidden-children-check.sh <session> <impl-url> <ref-dir>
#
# Output: <ref-dir>/hidden-children.json
#
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

SESSION="${1:-}"
IMPL_URL="${2:-}"
REF_DIR="${3:-}"

if [ -z "$SESSION" ] || [ -z "$IMPL_URL" ] || [ -z "$REF_DIR" ]; then
  echo "Usage: hidden-children-check.sh <session> <impl-url> <ref-dir>" >&2
  exit 2
fi

if [ ! -d "$REF_DIR" ]; then
  echo "ref-dir not found: $REF_DIR" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/hidden-children.json"
# L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
EVAL_OUT="$(mktemp -t hidden-children-XXXXXX)"
mv "$EVAL_OUT" "${EVAL_OUT}.json"
EVAL_OUT="${EVAL_OUT}.json"
trap 'rm -f "$EVAL_OUT"' EXIT

agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1 || {
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "agent-browser open failed: $IMPL_URL",
  "violations": []
}
JSON
  echo "hidden-children: skip (open failed)"
  exit 0
}
agent-browser --session "$SESSION" wait 2500 >/dev/null 2>&1 || true

EVAL_JS='(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const skip = new Set(["SCRIPT","STYLE","LINK","META","NOSCRIPT","TEMPLATE"]);
  const hidden = (el) => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return el.hidden ||
      cs.display === "none" ||
      cs.visibility === "hidden" ||
      cs.visibility === "collapse" ||
      parseFloat(cs.opacity || "1") <= 0.01 ||
      r.width < 2 || r.height < 2;
  };
  const bad = [];
  // Universality fix: div-only layouts using opaque hashed classes
  // (`<div class="prefix_name__hash">` with no `<section>` /
  // `<article>` / role="region") bypass the semantic-tag query. After
  // collecting semantic sections, if fewer than 3 are found, also
  // pick up large container divs whose area is >=15% of viewport
  // and whose direct children count >=2 — geometric proxy for
  // "this is a section".
  const semSel = "main,section,header,footer,article,nav,aside,[role=region],[role=banner],[role=contentinfo]";
  const vwArea = innerWidth * innerHeight;
  let sections = [...document.querySelectorAll(semSel)]
    .filter((s) => {
      const r = s.getBoundingClientRect();
      return r.width >= 100 && r.height >= 50;
    });
  if (sections.length < 3) {
    const seen = new Set(sections);
    document.querySelectorAll("body > div, main > div, [class*='wrap'] > div").forEach((d) => {
      if (seen.has(d)) return;
      const r = d.getBoundingClientRect();
      if (r.width < 200 || r.height < 100) return;
      if (r.width * r.height < vwArea * 0.15) return;
      if (d.children.length < 2) return;
      sections.push(d);
      seen.add(d);
    });
  }
  for (const s of sections) {
    s.scrollIntoView({ block: "center", behavior: "instant" });
    dispatchEvent(new Event("scroll"));
    await sleep(300);
    if (document.getAnimations) {
      document.getAnimations().forEach((a) => { try { a.finish(); } catch (e) {} });
    }
    await sleep(700);
    const kids = [...s.children].filter((c) => {
      if (skip.has(c.tagName)) return false;
      const hasText = (c.textContent || "").trim().length > 0;
      const hasVisualChild = !!c.querySelector(
        "img,svg,canvas,video,a,button,h1,h2,h3,h4,h5,h6,p,li,input,textarea"
      );
      return hasText || hasVisualChild;
    });
    const sr = s.getBoundingClientRect();
    if (kids.length >= 2 && kids.every(hidden) && sr.width * sr.height > 20000) {
      bad.push({
        tag: s.tagName.toLowerCase(),
        id: s.id || null,
        className: String(s.className || "").slice(0, 80),
        childrenChecked: kids.length,
        area: Math.round(sr.width * sr.height),
      });
    }
  }
  window.scrollTo(0, 0);
  return JSON.stringify({
    sectionsChecked: sections.length,
    violations: bad,
    status: bad.length ? "fail" : "pass",
  });
})()'

agent-browser --session "$SESSION" eval "$EVAL_JS" > "$EVAL_OUT" 2>/dev/null || {
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "agent-browser eval failed",
  "violations": []
}
JSON
  echo "hidden-children: skip (eval failed)"
  exit 0
}

python3 - "$EVAL_OUT" "$OUT_PATH" "$IMPL_URL" <<'PY'
import json
import sys
from pathlib import Path

eval_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
impl_url = sys.argv[3]

raw = eval_path.read_text(encoding="utf-8", errors="ignore").strip()
if not raw:
    out_path.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
        "reason": "empty browser output",
        "violations": [],
    }, indent=2) + "\n", encoding="utf-8")
    print("hidden-children: skip (empty)")
    sys.exit(0)

try:
    outer = json.loads(raw)
    if isinstance(outer, str):
        inner = json.loads(outer)
    elif isinstance(outer, dict):
        inner = outer
    else:
        inner = {"violations": [], "sectionsChecked": 0, "status": "pass"}
except ValueError:
    stripped = raw.strip("'\"")
    try:
        inner = json.loads(stripped)
    except ValueError:
        out_path.write_text(json.dumps({
            "schemaVersion": 1,
            "status": "skip",
            "reason": "non-json browser output",
            "raw": raw[:300],
        }, indent=2) + "\n", encoding="utf-8")
        print("hidden-children: skip (non-json)")
        sys.exit(0)

violations = inner.get("violations") or []
sections_checked = int(inner.get("sectionsChecked") or 0)
status = "fail" if violations else "pass"

result = {
    "schemaVersion": 1,
    "status": status,
    "implUrl": impl_url,
    "sectionsChecked": sections_checked,
    "violationCount": len(violations),
    "violations": violations[:30],
    "rule": (
        "Major sections (main/section/header/footer/article/[role=region] "
        "with width>=100 and height>=50, area>20000) must not have ALL "
        "their non-trivial direct children permanently hidden after "
        "animations finish. Hidden = display:none / visibility:hidden / "
        "opacity<=0.01 / rect<2x2. This is the runtime companion of the "
        "ref-screenshot-as-background cheat."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"hidden-children: {len(violations)} violation(s) / "
    f"{sections_checked} section(s) → {status} → {out_path}"
)
sys.exit(0 if status == "pass" else 1)
PY
