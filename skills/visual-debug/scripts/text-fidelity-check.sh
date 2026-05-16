#!/usr/bin/env bash
# text-fidelity-check.sh — block Phase-4 fabrication of visible text.
#
# Compares JSX text-position strings in `<impl>/src/components/*.tsx` against
# the verbatim text allowlist in `<ref>/dom-scaffold.json` (Fix 8). Any string
# the impl renders in a JSX text position that is NOT in the ref allowlist
# is flagged as fabrication and the gate fails.
#
# This closes the failure mode where Phase 4 invents text like "Eat Real
# Food" / "Dietary Guidelines" when ref says "Real Food Wins" / "America is
# the greatest country on Earth". Pattern is from Design2Code (arXiv:
# 2403.03163) — constrain-then-verify: extract ground truth as an allowlist,
# post-validate generated code against it, fail-fast on drift.
#
# Usage:
#   text-fidelity-check.sh <ref-dir> <impl-dir> [--out <json>]
#
# Exit 0 on pass (no fabrication), 1 on fabrication detected, 2 on error.
set -euo pipefail

REF_DIR=""
IMPL_DIR=""
OUT_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      OUT_PATH="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *)
      if [[ -z "$REF_DIR" ]]; then
        REF_DIR="$1"
      elif [[ -z "$IMPL_DIR" ]]; then
        IMPL_DIR="$1"
      else
        echo "text-fidelity: unexpected arg: $1" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

if [[ -z "$REF_DIR" ]]; then
  echo "usage: text-fidelity-check.sh <ref-dir> [<impl-dir>] [--out <json>]" >&2
  exit 2
fi
if [[ -z "$IMPL_DIR" ]]; then
  # Auto-detect impl-dir from sibling of ref-dir (benchmark/work/<sha>/{ref,impl}).
  IMPL_DIR="$(cd "$(dirname "$REF_DIR")" && pwd)/impl"
fi
SCAFFOLD="$REF_DIR/dom-scaffold.json"
if [[ ! -f "$SCAFFOLD" ]]; then
  echo "text-fidelity: dom-scaffold.json missing — run dom-scaffold.sh first" >&2
  exit 2
fi
if [[ ! -d "$IMPL_DIR" ]]; then
  echo "text-fidelity: impl dir not found: $IMPL_DIR" >&2
  exit 2
fi

python3 - "$SCAFFOLD" "$IMPL_DIR" "${OUT_PATH:-}" <<'PY'
import json
import re
import sys
from pathlib import Path


scaffold_path = Path(sys.argv[1])
impl_dir = Path(sys.argv[2])
out_path = Path(sys.argv[3]) if sys.argv[3] else None


# Build allowlist from scaffold: collect every `text` field from the tree.
scaffold = json.loads(scaffold_path.read_text(encoding="utf-8"))
allowed_strings = set()


def walk(node, depth=0):
    if depth > 12 or not isinstance(node, dict):
        return
    text = node.get("text")
    if isinstance(text, str) and text.strip():
        # Normalize: collapse whitespace, strip. We compare on normalized form
        # so trailing-space variants don't false-positive.
        norm = re.sub(r"\s+", " ", text).strip()
        if norm:
            allowed_strings.add(norm)
            # Also allow individual newline-split lines (some JSX renders
            # "Real Food Wins" as two lines: "Real Food" and "Wins").
            for line in re.split(r"[\r\n]+", text):
                line = line.strip()
                if line:
                    allowed_strings.add(line)
    for child in node.get("children", []) or []:
        walk(child, depth + 1)


walk(scaffold.get("tree", {}))

# Also accept short/common strings — these are noise from the allowlist diff
# perspective (numbers, single words like "Get started", etc.). We're after
# real semantic phrases the agent could fabricate.
def is_meaningful(s):
    """Filter for strings worth checking — long enough to be content,
    not punctuation or boilerplate."""
    if len(s) < 8:
        return False
    if not re.search(r"[A-Za-z]{4,}", s):
        return False
    # Skip JSX attribute boilerplate.
    if re.fullmatch(r"[\w-]+", s):
        return False
    return True


# Extract JSX text-position strings from each impl component.
# JSX text positions are content between `>` and `<` that isn't itself a tag,
# OR content inside `{"..."}` expressions used for verbatim render. Common
# patterns:
#   <h1>Real Food Wins</h1>         → "Real Food Wins"
#   <p>{"America is..."}</p>        → "America is..."
#   <p>{`Multi\nline`}</p>          → "Multi\nline"
#   alt="hero image"                → "hero image"  (also visible to users)
#   title="..."                     → "..."         (tooltip; visible)
#
# We use regexes (not a TSX parser) to keep this dependency-free. Conservative:
# only match patterns we're confident are visible text.
JSX_TEXT_PATTERNS = [
    # Plain JSX text: >Some Text<
    # Avoid capturing JSX expressions ({foo}), JSX comments, or attribute fragments.
    re.compile(r">([^<>{}\n][^<>{}]*?[^<>{}\s])<"),
    # JSX inline string literals: >{"some text"}< or >{'some text'}<
    re.compile(r"\{\s*[\"']([^\"'{}\n]+)[\"']\s*\}"),
    # alt= / title= attributes (user-visible)
    re.compile(r"\b(?:alt|title|aria-label|placeholder)\s*=\s*[\"']([^\"'\n]+)[\"']"),
]


impl_components = sorted((impl_dir / "src" / "components").glob("*.tsx"))
if not impl_components:
    # No components produced yet — gate is informational, not failing.
    out = {
        "status": "pass",
        "reason": "no components yet — Phase 4 not run",
        "components_checked": 0,
        "fabrications": [],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(0)


fabrications = []
total_meaningful = 0


for comp_path in impl_components:
    body = comp_path.read_text(encoding="utf-8")
    # Strip JSX comments and JS line/block comments — cheap regex pass.
    body_clean = re.sub(r"/\*[\s\S]*?\*/", "", body)
    body_clean = re.sub(r"//[^\n]*", "", body_clean)

    seen = set()
    for pat in JSX_TEXT_PATTERNS:
        for m in pat.finditer(body_clean):
            raw = m.group(1)
            norm = re.sub(r"\s+", " ", raw).strip()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            if not is_meaningful(norm):
                continue
            total_meaningful += 1
            # Substring tolerance: scaffold has "Real Food Wins"; impl may
            # split into "Real Food" + "Wins". Accept if norm is a substring
            # of any allowed string, OR any allowed string is a substring of
            # norm (multi-line concatenation).
            ok = False
            for allowed in allowed_strings:
                if norm == allowed or norm in allowed or allowed in norm:
                    ok = True
                    break
            if not ok:
                fabrications.append({
                    "component": comp_path.name,
                    "text": norm[:160],
                })


status = "fail" if fabrications else "pass"
out = {
    "status": status,
    "components_checked": len(impl_components),
    "total_meaningful_strings": total_meaningful,
    "allowlist_size": len(allowed_strings),
    "fabrications_count": len(fabrications),
    "fabrications": fabrications[:50],  # cap output
    "rule": (
        "Every meaningful JSX text-position string in impl/src/components/ "
        "must appear (verbatim or as a substring relation) in the "
        "dom-scaffold.json allowlist. Inventing text not in the allowlist "
        "is fabrication and fails this gate."
    ),
}
print(json.dumps(out, indent=2, ensure_ascii=False))
if out_path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
sys.exit(0 if status == "pass" else 1)
PY
