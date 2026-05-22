#!/usr/bin/env bash
# text-fidelity-check.sh — block Phase-4 fabrication of visible text.
#
# Compares JSX text-position strings in `<impl>/src/**/*.tsx` against the
# verbatim text in `<ref>/dom-scaffold.json` (Fix 8). Any string the impl
# renders in a JSX text position that is NOT in the ref allowlist is flagged
# as fabrication. Any meaningful scaffold text that the impl omits is flagged
# as missing. Either condition fails the gate.
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
# Exit 0 on pass (no fabrication and no missing source text), 1 on fidelity
# failure, 2 on error.
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
required_strings = set()


def walk(node, depth=0):
    if depth > 12 or not isinstance(node, dict):
        return
    # Symmetric to the impl-side <script> strip below: dom-scaffold.json
    # captures every node's text, including Next.js RSC payloads and runtime
    # polyfill bodies inside <script> tags. Those bodies (e.g.
    # `self.__next_f.push(...)`, `$RB=[];$RV=function...`) are not
    # user-visible content the impl is expected to reproduce, but without
    # this filter the bidirectional check flags them as "missing" forever.
    # Mirror the dom-extraction skip list (script/style/noscript/template).
    tag = node.get("tag", "")
    if isinstance(tag, str) and tag.lower() in {"script", "style", "noscript", "template"}:
        return
    text = node.get("text")
    if isinstance(text, str) and text.strip():
        # Normalize: collapse whitespace, strip. We compare on normalized form
        # so trailing-space variants don't false-positive.
        norm = re.sub(r"\s+", " ", text).strip()
        if norm:
            allowed_strings.add(norm)
            required_strings.add(norm)
            # Also allow individual newline-split lines (some JSX renders
            # "Real Food Wins" as two lines: "Real Food" and "Wins").
            for line in re.split(r"[\r\n]+", text):
                line = line.strip()
                if line:
                    allowed_strings.add(line)
                    required_strings.add(line)
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
    # alt= / title= / aria-label / placeholder attributes (user-visible)
    re.compile(r"\b(?:alt|title|aria-label|placeholder)\s*=\s*[\"']([^\"'\n]+)[\"']"),
    re.compile(r"\b(?:label|heading|subheading|title|subtitle|description|caption|name|content|copy|message|text)\s*=\s*[\"']([^\"'\n]+)[\"']"),
]


SCAN_EXCLUDE = {"node_modules", ".next", "dist", "build", ".turbo"}
all_tsx = []
src_root = impl_dir / "src"
if src_root.is_dir():
    for p in src_root.rglob("*.tsx"):
        if any(part in SCAN_EXCLUDE for part in p.parts):
            continue
        all_tsx.append(p)
impl_components = sorted(all_tsx)
required_meaningful = sorted(s for s in required_strings if is_meaningful(s))
if not impl_components:
    status = "fail" if required_meaningful else "pass"
    out = {
        "status": status,
        "reason": (
            "no components found but scaffold has meaningful text"
            if required_meaningful else "no components yet — no meaningful scaffold text"
        ),
        "components_checked": 0,
        "required_meaningful_strings": len(required_meaningful),
        "missing_count": len(required_meaningful),
        "missing": [{"text": s[:160]} for s in required_meaningful[:50]],
        "fabrications": [],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(0 if status == "pass" else 1)


fabrications = []
impl_strings = []
total_meaningful = 0


for comp_path in impl_components:
    body = comp_path.read_text(encoding="utf-8")
    # Strip JSX comments and JS line/block comments — cheap regex pass.
    body_clean = re.sub(r"/\*[\s\S]*?\*/", "", body)
    body_clean = re.sub(r"//[^\n]*", "", body_clean)
    # Validation run finding: Next.js App Router RSC hydration payloads
    # appear inside <script> tags as `self.__next_f.push([1, "..."])`
    # — large fragments of JSON-encoded server output that text-
    # fidelity flagged as non-verbatim impl text. Strip <script> and
    # <style> blocks before extracting JSX text positions; these
    # blocks never carry user-visible copy.
    body_clean = re.sub(
        r"<script\b[^>]*>[\s\S]*?</script\s*>", "", body_clean,
        flags=re.IGNORECASE,
    )
    body_clean = re.sub(
        r"<style\b[^>]*>[\s\S]*?</style\s*>", "", body_clean,
        flags=re.IGNORECASE,
    )

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
            impl_strings.append(norm)
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


impl_blob = " ".join(impl_strings)
impl_word_set: set[str] = set()
for s in impl_strings:
    for word in re.findall(r"[A-Za-z0-9']+", s.lower()):
        if len(word) >= 3:  # skip articles, single letters
            impl_word_set.add(word)
missing = []
for required in required_meaningful:
    # Exact/source-order preservation check. The full required phrase must
    # appear in one rendered text node or across adjacent rendered text nodes.
    if required in impl_strings or required in impl_blob:
        continue
    # Relaxed: 90%+ token coverage across the impl src tree. Catches
    # split-but-rendered phrases without admitting omissions.
    required_words = [w for w in re.findall(r"[A-Za-z0-9']+", required.lower())
                      if len(w) >= 3]
    if required_words:
        hits = sum(1 for w in required_words if w in impl_word_set)
        if hits / len(required_words) >= 0.9:
            continue
    missing.append({"text": required[:160]})


status = "fail" if fabrications or missing else "pass"
out = {
    "status": status,
    "components_checked": len(impl_components),
    "total_meaningful_strings": total_meaningful,
    "required_meaningful_strings": len(required_meaningful),
    "allowlist_size": len(allowed_strings),
    "fabrications_count": len(fabrications),
    "fabrications": fabrications[:50],  # cap output
    "missing_count": len(missing),
    "missing": missing[:50],
    "rule": (
        "Every meaningful JSX text-position string in impl/src/ must appear "
        "in the dom-scaffold.json allowlist, and every meaningful scaffold "
        "text string must be rendered by the impl. Invented text and omitted "
        "source text both fail this gate."
    ),
}
print(json.dumps(out, indent=2, ensure_ascii=False))
if out_path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
sys.exit(0 if status == "pass" else 1)
PY
