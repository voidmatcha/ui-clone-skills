#!/usr/bin/env bash
# scaffold-residue-check.sh — fail when impl source imports React
# components that are never rendered anywhere (dead-scaffold residue).
#
#
# Detection: for every component file under impl/src/, check that the
# exported component name is referenced in JSX (`<Name>` or `<Name `
# or `<Name/>`) in at least one OTHER component file, OR in the entry
# point file. Components that are imported in some file but never
# wrapped in `<Name`... = orphan.
#
# This is intentionally narrow — only flags components that exist but
# have zero JSX references. Components used in conditional rendering,
# imported via dynamic, or rendered via createElement all still count
# as used (createElement(Name, ...) matches the Name pattern).
#
# Usage:
#   scaffold-residue-check.sh <ref-dir> [<impl-root>]
#
# Output: <ref-dir>/scaffold-residue.json
#   { status, implRoot, orphanCount, orphans: [{component, file}] }
#
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: scaffold-residue-check.sh <ref-dir> [<impl-root>]" >&2
  exit 2
fi

if [ -z "$IMPL_ROOT" ]; then
  PLUGIN_ROOT_CAND="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
  for cand_root in "$PLUGIN_ROOT_CAND" "$(cd "$(dirname "$0")/../../.." && pwd)"; do
    [ -z "$cand_root" ] && continue
    RESOLVER="$cand_root/scripts/extract/find-impl-root.sh"
    if [ -f "$RESOLVER" ]; then
      IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
      [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT" ] && break
    fi
  done
fi

OUT_PATH="$REF_DIR/scaffold-residue.json"

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl_root not found",
  "orphans": []
}
JSON
  echo "scaffold-residue: skip (no impl)"
  exit 0
fi

python3 - "$IMPL_ROOT" "$OUT_PATH" <<'PY'
import json
import re
import sys
from pathlib import Path

impl_root = Path(sys.argv[1])
out_path = Path(sys.argv[2])

# Codex universality audit MEDIUM: scaffold-residue only knows about
# React. For Vue/Svelte/Astro impls there's no PascalCase "component"
# in the React sense — every legitimate clone would falsely PASS
# (no orphans found because no components found). Detect stack from
# impl/package.json + characteristic source files; if non-React,
# skip with reason rather than emit misleading "pass".
def _detect_stack(impl: Path) -> str:
    pkg_json = impl / "package.json"
    deps: dict[str, str] = {}
    if pkg_json.is_file():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                d = pkg.get(key) or {}
                if isinstance(d, dict):
                    deps.update({k: str(v) for k, v in d.items()})
        except (OSError, ValueError):
            pass
    if "react" in deps:
        return "react"
    if "vue" in deps or "@vue/runtime-dom" in deps:
        return "vue"
    if "svelte" in deps:
        return "svelte"
    if "solid-js" in deps:
        return "solid"
    if "astro" in deps:
        return "astro"
    if "@remix-run/react" in deps:
        return "react"  # Remix is React-based
    # Source-file fallback: if any .vue/.svelte/.astro exists, route there.
    for suf in (".vue", ".svelte", ".astro"):
        if any(impl.rglob(f"*{suf}")):
            return suf.lstrip(".")
    return "unknown"

stack = _detect_stack(impl_root)
if stack not in ("react", "unknown"):
    out_path.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
        "reason": f"non-React stack ({stack}) — scaffold-residue is React-specific",
        "implRoot": str(impl_root),
        "stack": stack,
        "orphans": [],
    }, indent=2) + "\n", encoding="utf-8")
    print(f"scaffold-residue: skip (stack={stack})")
    sys.exit(0)

SRC = impl_root / "src"
if not SRC.is_dir():
    out_path.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
        "reason": "impl/src not found",
        "implRoot": str(impl_root),
        "orphans": [],
    }, indent=2) + "\n", encoding="utf-8")
    print("scaffold-residue: skip (no impl/src)")
    sys.exit(0)

EXCLUDE = {"node_modules", ".next", "dist", "build", ".turbo", ".cache"}
SUFFIXES = {".tsx", ".jsx", ".ts", ".js"}

# Capture exported component names. Patterns:
#   export default function Name(...)
#   export function Name(...)
#   export const Name = (...) => / function (...
#   export default Name
#   const Name = (...) => ...; export default Name
EXPORT_PATTERNS = [
    re.compile(r"export\s+default\s+function\s+([A-Z][A-Za-z0-9_]*)"),
    re.compile(r"export\s+function\s+([A-Z][A-Za-z0-9_]*)"),
    re.compile(
        r"export\s+const\s+([A-Z][A-Za-z0-9_]*)\s*[:=]\s*"
        r"(?:\([^)]*\)|function|React\.memo|memo|forwardRef)"
    ),
    re.compile(r"export\s+default\s+([A-Z][A-Za-z0-9_]*)\s*[;\n]"),
]


def list_source_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in SUFFIXES:
            continue
        if any(part in EXCLUDE for part in p.parts):
            continue
        out.append(p)
    return out


sources = list_source_files(SRC)
# Files of interest: anything that exports a PascalCase component.
component_files: dict[str, Path] = {}
for path in sources:
    text = path.read_text(encoding="utf-8", errors="ignore")
    for pat in EXPORT_PATTERNS:
        for m in pat.finditer(text):
            name = m.group(1)
            # Skip helpers that don't look like a renderable component.
            # PascalCase + capital first letter is the React convention.
            component_files.setdefault(name, path)


if not component_files:
    out_path.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "reason": "no PascalCase component exports found — nothing to check",
        "implRoot": str(impl_root),
        "orphans": [],
    }, indent=2) + "\n", encoding="utf-8")
    print("scaffold-residue: pass (no components)")
    sys.exit(0)


# Find JSX usage of each component anywhere in the impl source.
# Usage = `<Name` followed by whitespace, `>`, `/`, or `\n` (an opening
# tag). createElement(Name, ...) also counts as usage.
def is_used(name: str, exclude_path: Path) -> bool:
    jsx_re = re.compile(rf"<\s*{re.escape(name)}\b")
    create_re = re.compile(rf"\bcreateElement\(\s*{re.escape(name)}\b")
    for path in sources:
        if path == exclude_path:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if jsx_re.search(text) or create_re.search(text):
            return True
    return False


orphans: list[dict] = []
# Common entry-point file names — if a component is ONLY defined in
# main.{tsx,jsx} or App.{tsx,jsx} and not used elsewhere, that's fine
# (entry-point self-rendering is the whole point).
ENTRY_FILENAMES = {
    "main.tsx", "main.jsx", "main.ts", "main.js",
    "App.tsx", "App.jsx", "App.ts", "App.js",
    "index.tsx", "index.jsx",
}

RE_EXPORT_PATTERNS = [
    re.compile(
        r"export\s*\{[^}]*\b{name}\b[^}]*\}",
    ),
    re.compile(
        r"export\s+\*\s+from\s+[\"']\./[^\"']+[\"']",
    ),
]


def is_re_exported(name: str) -> bool:
    rep_re = re.compile(
        r"export\s*\{[^}]*\b" + re.escape(name) + r"\b[^}]*\}",
    )
    for path in sources:
        fname = path.name.lower()
        if fname not in {"index.ts", "index.tsx", "index.js", "index.jsx",
                         "registry.ts", "registry.tsx"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if rep_re.search(text):
            return True
    return False


for name, defining_file in sorted(component_files.items()):
    if defining_file.name in ENTRY_FILENAMES:
        continue
    if is_used(name, defining_file):
        continue
    # Re-exported via barrel / registry — not an orphan, this is
    # intentional public API surface for a UI library.
    if is_re_exported(name):
        continue
    orphans.append({
        "component": name,
        "file": str(defining_file.relative_to(impl_root)),
    })


# Tolerance: a single orphan can be a stale leftover from a refactor.
total_components = len(component_files) - sum(
    1 for n, p in component_files.items() if p.name in ENTRY_FILENAMES
)
orphan_count = len(orphans)
orphan_ratio = orphan_count / total_components if total_components else 0.0
status = "fail" if (orphan_count >= 3 or (orphan_count >= 1 and orphan_ratio >= 0.4)) else "pass"

result = {
    "schemaVersion": 1,
    "status": status,
    "implRoot": str(impl_root),
    "totalComponents": total_components,
    "orphanCount": orphan_count,
    "orphanRatio": round(orphan_ratio, 3),
    "orphans": orphans[:50],
    "rule": (
        "PascalCase components exported from impl/src/ (excluding entry "
        "files main.*/App.*/index.{tsx,jsx}) must appear as JSX usage "
        "(`<Name` / `createElement(Name`) somewhere in the impl tree. "
        "≥3 orphans OR ≥40% orphan ratio = scaffold residue cheat."
    ),
}
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"scaffold-residue: {orphan_count}/{total_components} orphan(s) "
    f"(ratio={orphan_ratio:.2f}) → {status} → {out_path}"
)
sys.exit(0 if status == "pass" else 1)
PY
