#!/usr/bin/env bash
# lottie-slot-identity-check.sh — Static gate: every transition-spec.json Lottie
# entry must be reproduced in the impl with the SAME container, asset, and
# loop/autoplay flags.
#
# Why it matters:
#   transition-spec.json carries the complete slot->asset map (container id,
#   path/mobilePath, loop, autoplay). The failure class this catches is the
#   navercorp clone: 5 JSONs copied and "referenced so required-media-coverage
#   passes", but only 2 slots actually mounted, with autoplay/loop inverted and
#   one asset dropped into an invented container. required-media-coverage sees
#   the string and passes; this gate parses the actual mount call sites and
#   fails on:
#     - no-mount          spec container never mounted in impl source
#     - container-missing  asset mounted, but not into the spec's container id
#     - asset-not-mounted  container exists, but the spec asset is never bound
#     - flag-mismatch      mounted, but loop/autoplay differ from the spec
#
#   Copied CSS / mirror dirs and comments never count as a mount — only real
#   loadAnimation()/mount() bindings in DOM-producing source do.
#
# Usage:
#   lottie-slot-identity-check.sh <ref-dir> [<impl-src-dir>]
#
# Exit: 0 = all slots match (or spec has no lottie entries), 1 = mismatches,
#       2 = setup error.

set -uo pipefail

COMP_DIR="${1:?Usage: lottie-slot-identity-check.sh <ref-dir> [<impl-dir>]}"
IMPL_DIR="${2:-}"
SPEC="$COMP_DIR/transition-spec.json"

if [ -z "$IMPL_DIR" ] || [ ! -d "$IMPL_DIR" ]; then
  RESOLVER="${PLUGIN_ROOT:-$(dirname "$(dirname "$(dirname "${BASH_SOURCE[0]}")")")}/scripts/extract/find-impl-root.sh"
  if [ -x "$RESOLVER" ]; then
    RESOLVED=$(bash "$RESOLVER" "$COMP_DIR" 2>/dev/null | sed -n '1p')
    if [ -n "$RESOLVED" ] && [ -d "$RESOLVED" ]; then
      IMPL_DIR="$RESOLVED"
    fi
  fi
fi

if [ ! -f "$SPEC" ]; then
  echo "ERROR: transition-spec.json not found at $SPEC" >&2
  exit 2
fi
if [ -z "$IMPL_DIR" ] || [ ! -d "$IMPL_DIR" ]; then
  echo "ERROR: impl source dir not found (tried arg + find-impl-root.sh fallback)" >&2
  exit 2
fi

# Mirror dirs excluded from mount scanning (a mount in copied CSS is not a mount,
# and CSS carries the container ids verbatim). Mirrors the sibling coverage
# scripts' UI_CLONE_GENERATED_EVIDENCE_DIRS convention.
MIRROR_DIRS="from-ref ref-css ${UI_CLONE_GENERATED_EVIDENCE_DIRS:-}"

python3 - "$SPEC" "$IMPL_DIR" "$COMP_DIR" "$MIRROR_DIRS" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

spec_path = Path(sys.argv[1])
impl_dir = Path(sys.argv[2])
comp_dir = Path(sys.argv[3])
mirror_dirs = {d for d in re.split(r"[,:\s]+", sys.argv[4]) if d}
out_path = comp_dir / "lottie-slot-identity.json"

try:
    spec = json.loads(spec_path.read_text())
except (OSError, json.JSONDecodeError) as e:
    print(f"ERROR: cannot read {spec_path}: {e}", file=sys.stderr)
    sys.exit(2)

transitions = spec.get("transitions") if isinstance(spec, dict) else None
transitions = transitions if isinstance(transitions, list) else []


def _is_lottie(entry: dict) -> bool:
    anim = entry.get("animation")
    if not isinstance(anim, dict):
        return False
    if str(anim.get("type", "")).lower() == "lottie":
        return True
    if str(anim.get("library", "")).lower() in ("bodymovin", "lottie", "lottie-web"):
        return True
    p = anim.get("path")
    return isinstance(p, str) and p.lower().endswith(".json") and "lottie" in p.lower()


lottie_entries = [t for t in transitions if isinstance(t, dict) and _is_lottie(t)]

VENDOR = {".git", "node_modules", "dist", "build", ".next", "coverage"}
SKIP_DIRS = VENDOR | mirror_dirs
SRC_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".html", ".htm"}


def _strip_comments(text: str) -> str:
    # Block comments (incl. /** */), then line comments — but keep `://` so
    # URLs survive. Good enough for mount-site detection; strings holding `/*`
    # are vanishingly rare in the generated/hand-written mount modules.
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"(?<!:)//[^\n]*", " ", text)
    return text


def _basename(p: str) -> str:
    return os.path.basename(p.strip())


# ── Read DOM-producing impl source (mirror/vendor/stylesheets excluded) ──────
sources: list[str] = []
for root, dirs, files in os.walk(impl_dir):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for name in files:
        if Path(name).suffix.lower() in SRC_EXT:
            try:
                sources.append(_strip_comments(Path(root, name).read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
joined = "\n".join(sources)

# ── Alias map: const/let/var NAME = { key: '<path>.json' } -> NAME.key basename ─
alias_map: dict[str, str] = {}
for m in re.finditer(r"\b(?:const|let|var)\s+(\w+)\s*=\s*\{", joined):
    var = m.group(1)
    # Grab the (brace-free) body of this object literal.
    body = joined[m.end():]
    depth = 1
    i = 0
    while i < len(body) and depth:
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
        i += 1
    block = body[: i - 1]
    for km in re.finditer(r"(\w+)\s*:\s*['\"]([^'\"]+\.json)['\"]", block):
        alias_map[f"{var}.{km.group(1)}"] = _basename(km.group(2))

# ── Mount tuples: (asset_basename, loop|None, autoplay|None) from flag objects ─
# A "mount unit" is any brace-object carrying both loop and autoplay. We read the
# literal flags (None when computed, e.g. `loop: opts.loop`) and bind the asset
# from the SAME enclosing call's argument list — a '<path>.json' literal or an
# alias member (LOTTIE.intro) resolved via alias_map. Scoping to the enclosing
# call (not a char window) is what keeps a neighbouring mount's asset from
# bleeding onto this one — e.g. `mount(x, LOTTIE.intro, {loop,autoplay})` binds
# intro to ITS flags, not the footer mount two lines down.
tuples: list[tuple[str, object, object]] = []
flag_obj_re = re.compile(r"\{[^{}]*\bloop\b[^{}]*\bautoplay\b[^{}]*\}|\{[^{}]*\bautoplay\b[^{}]*\bloop\b[^{}]*\}")
lit_json_re = re.compile(r"['\"]([^'\"]+\.json)['\"]")
alias_ref_re = re.compile(r"\b(\w+\.\w+)\b")


def _lit_bool(block: str, key: str):
    mm = re.search(rf"\b{key}\s*:\s*(true|false)\b", block)
    return None if mm is None else (mm.group(1) == "true")


def _enclosing_call_args(text: str, start: int, end: int) -> str:
    """Argument list of the call that lexically encloses text[start:end] (the
    flag object). Falls back to the flag object itself when it is a bare object
    literal, not a call argument."""
    i, depth, open_paren = start - 1, 0, -1
    while i >= 0:
        c = text[i]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                open_paren = i
                break
            depth -= 1
        elif c in ";={}" and depth == 0:
            break
        i -= 1
    if open_paren < 0:
        return text[start:end]
    j, depth, close_paren = end, 0, -1
    while j < len(text):
        c = text[j]
        if c == "(":
            depth += 1
        elif c == ")":
            if depth == 0:
                close_paren = j
                break
            depth -= 1
        elif c == ";" and depth == 0:
            break
        j += 1
    if close_paren < 0:
        close_paren = min(len(text), end + 400)
    return text[open_paren + 1:close_paren]


for fm in flag_obj_re.finditer(joined):
    block = fm.group(0)
    loop = _lit_bool(block, "loop")
    autoplay = _lit_bool(block, "autoplay")
    args = _enclosing_call_args(joined, fm.start(), fm.end())
    assets: set[str] = set()
    for lm in lit_json_re.finditer(args):
        assets.add(_basename(lm.group(1)))
    for am in alias_ref_re.finditer(args):
        if am.group(1) in alias_map:
            assets.add(alias_map[am.group(1)])
    for a in assets:
        tuples.append((a, loop, autoplay))


def _container_present(target: str) -> bool:
    ids = re.findall(r"#([A-Za-z_][\w-]*)", target)
    classes = re.findall(r"\.([A-Za-z_][\w-]*)", target)
    for tok in ids:
        esc = re.escape(tok)
        if re.search(rf"['\"]#?{esc}['\"]", joined) or re.search(rf"#{esc}(?![\w-])", joined) \
                or re.search(rf"\bid\s*[:=]\s*['\"]{esc}['\"]", joined):
            return True
    for tok in classes:
        esc = re.escape(tok)
        if re.search(rf"['\"]\.?{esc}['\"]", joined) or re.search(rf"\.{esc}(?![\w-])", joined):
            return True
    # target with neither #id nor .class (bare tag) is not checkable — treat as
    # present so a tag-only container is not falsely reported absent.
    return not ids and not classes


problems = []
matched = 0
for e in lottie_entries:
    eid = str(e.get("id") or "?")
    anim = e.get("animation") if isinstance(e.get("animation"), dict) else {}
    target = e.get("target") or e.get("selector") or ""
    exp_loop = bool(anim.get("loop"))
    exp_autoplay = bool(anim.get("autoplay"))
    spec_bases = set()
    for key in ("path", "mobilePath"):
        v = anim.get(key)
        if isinstance(v, str) and v.strip():
            spec_bases.add(_basename(v))

    container_ok = bool(target) and _container_present(str(target))
    asset_tuples = [t for t in tuples if t[0] in spec_bases]

    if not container_ok and not asset_tuples:
        problems.append({"id": eid, "container": target, "expectedPath": sorted(spec_bases),
                         "reason": "no-mount",
                         "detail": "spec container never mounted and asset never bound in impl source"})
        continue
    if not container_ok:
        problems.append({"id": eid, "container": target, "expectedPath": sorted(spec_bases),
                         "reason": "container-missing",
                         "detail": "asset is mounted but the spec container id is absent from impl source"})
        continue
    if not asset_tuples:
        problems.append({"id": eid, "container": target, "expectedPath": sorted(spec_bases),
                         "reason": "asset-not-mounted",
                         "detail": "container exists but the spec asset is never passed to a loadAnimation mount"})
        continue
    # Flags: at least one mount of this asset must carry matching literal flags.
    ok = any(t[1] == exp_loop and t[2] == exp_autoplay for t in asset_tuples)
    if not ok:
        def _js(v: object) -> str:
            return "null" if v is None else ("true" if v else "false")
        found = sorted({f"loop={_js(t[1])},autoplay={_js(t[2])}" for t in asset_tuples})
        problems.append({"id": eid, "container": target, "expectedPath": sorted(spec_bases),
                         "reason": "flag-mismatch",
                         "detail": f"expected loop={_js(exp_loop)},autoplay={_js(exp_autoplay)}; found {found}"})
        continue
    matched += 1

total = len(lottie_entries)
if total == 0:
    status = "skip"
elif problems:
    status = "fail"
else:
    status = "pass"

out = {"schemaVersion": 1, "status": status, "total": total,
       "matched": matched, "problems": problems}
out_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

print("═══ Lottie Slot Identity ═══")
print(f"Spec:        {spec_path}")
print(f"Impl source: {impl_dir}")
print(f"Lottie slots: {total}  matched: {matched}  problems: {len(problems)}")
for p in problems:
    print(f"  ❌ {p['id']:<16} [{p['reason']}] {p['detail']}")
if status == "skip":
    print("▸ no lottie entries in spec — nothing to check")
    sys.exit(0)
if status == "fail":
    print("")
    print("⛔ Lottie slot identity mismatch. Each spec entry must mount its EXACT")
    print("   container + asset with matching loop/autoplay. Regenerate mounts with")
    print("   scripts/extract/emit-lottie-mounts.sh (deterministic) instead of")
    print("   hand-authoring loadAnimation bindings.")
    sys.exit(1)
print("✅ Every spec Lottie slot mounts its exact container/asset with matching flags.")
sys.exit(0)
PY
