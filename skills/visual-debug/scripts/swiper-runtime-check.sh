#!/usr/bin/env bash
# swiper-runtime-check.sh — Block copied Swiper classes without Swiper runtime.
#
# Usage:
#   bash swiper-runtime-check.sh <ref-dir> <impl-root>
#
# Output:
#   <ref-dir>/swiper-runtime.json

set -uo pipefail

REF_DIR="${1:?Usage: swiper-runtime-check.sh <ref-dir> <impl-root>}"
IMPL_ROOT="${2:?Missing impl-root}"
OUT="$REF_DIR/swiper-runtime.json"
mkdir -p "$REF_DIR"

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_root = Path(sys.argv[2])
out_path = Path(sys.argv[3])

SKIP_DIRS = {"node_modules", ".next", "dist", "build", "coverage", ".git"}
REF_EXTS = {".js", ".json", ".css", ".html", ".txt"}
IMPL_EXTS = {".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".json"}
REF_SWIPER_RE = re.compile(r"\bSwiper\b|swiper-wrapper|swiper-slide|swiper\.bundle|swiper/css", re.IGNORECASE)
CLASS_RE = re.compile(r"swiper-wrapper|swiper-slide", re.IGNORECASE)
RUNTIME_RE = re.compile(
    r"from\s+['\"]swiper(?:/react)?['\"]|require\(\s*['\"]swiper(?:/react)?['\"]\s*\)"
    r"|new\s+Swiper\s*\(|<\s*Swiper\b",
    re.IGNORECASE,
)
# Genuine "explicit extracted sizing logic" is Swiper CONFIG the impl author
# wrote (spaceBetween / slidesPerView / marginRight). `translate3d` and
# `swiper-slide-active` are NOT sizing logic — they are baked-capture residue (the
# transpiler froze the running Swiper's inline transform and its runtime
# active-slide class), so counting them here let a dead, runtime-less baked
# carousel — exactly what this gate exists to block — evade class_only (F5).
INLINE_SIZE_RE = re.compile(r"spaceBetween|slidesPerView|marginRight", re.IGNORECASE)


def read_limited(path: Path, limit: int = 1_000_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def iter_files(root: Path, exts: set[str]) -> list[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in exts:
            out.append(path)
    return out

ref_text = "\n".join(read_limited(path, 250_000) for path in iter_files(ref_dir, REF_EXTS))
requires = bool(REF_SWIPER_RE.search(ref_text))
impl_text = "\n".join(read_limited(path, 400_000) for path in iter_files(impl_root, IMPL_EXTS))
package_json = impl_root / "package.json"
package_text = read_limited(package_json, 200_000)
has_dependency = '"swiper"' in package_text or "'swiper'" in package_text
has_runtime = bool(RUNTIME_RE.search(impl_text))
has_classes = bool(CLASS_RE.search(impl_text))
has_sizing_logic = bool(INLINE_SIZE_RE.search(impl_text))
class_only = has_classes and not has_runtime and not has_sizing_logic
issues: list[dict[str, object]] = []

if requires:
    if class_only:
        issues.append({"kind": "copied-swiper-classes", "message": "Impl copied swiper-wrapper/swiper-slide classes without initializing Swiper runtime."})
    if not has_dependency and has_runtime:
        issues.append({"kind": "missing-swiper-dependency", "message": "Impl references Swiper runtime but package.json lacks swiper dependency."})
    if not has_runtime and not has_sizing_logic:
        issues.append({"kind": "missing-swiper-sizing", "message": "Ref Swiper sizing/translate must be recreated with Swiper runtime or explicit extracted sizing logic."})

status = "fail" if issues else "pass"
if not requires:
    status = "skip"

artifact = {
    "schemaVersion": 1,
    "status": status,
    "requiresSwiper": requires,
    "hasDependency": has_dependency,
    "hasRuntime": has_runtime,
    "hasClasses": has_classes,
    "hasSizingLogic": has_sizing_logic,
    "classOnly": class_only,
    "issues": issues,
}
out_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
if status == "fail":
    print(f"❌ Swiper runtime: FAIL ({len(issues)} issue(s))")
    sys.exit(1)
print(f"✅ Swiper runtime: {status.upper()}")
PY
